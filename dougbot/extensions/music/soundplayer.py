import asyncio
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from nextcord.embeds import Embed
from nextcord.ext import commands
from youtube_search import YoutubeSearch

from dougbot.common import voiceutils
from dougbot.common.logger import Logger
from dougbot.common.messaging import reactions
from dougbot.config import EXTENSION_RESOURCES_DIR
from dougbot.core.bot import DougBot
from dougbot.extensions.common import webutils
from dougbot.extensions.common.annotation.miccheck import voice_command
from dougbot.extensions.common.audio.youtubedl import YouTubeDL
from dougbot.extensions.common.file import fileutils
from dougbot.extensions.music.soundconsumer import SoundConsumer
from dougbot.extensions.music.track import Track


class SoundPlayer(commands.Cog):
    CLIP_DIR = os.path.join(EXTENSION_RESOURCES_DIR, 'music', 'audio')
    CACHE_DIR = os.path.join(EXTENSION_RESOURCES_DIR, 'music', 'cache')
    THREAD_POOL: ThreadPoolExecutor = ThreadPoolExecutor()

    def __init__(self, bot: DougBot):
        self.bot = bot
        self.loop = self.bot.loop
        self.bot.event(self.on_voice_state_update)

        self._order_lock = asyncio.Lock()  # Keeps order tracks are played in.
        self._volume = 1.0

        self._last_embed_message = None
        self._url = ''
        self._title = ''
        self._thumbnail = ''
        self._duration = 0

        self._yt_downloader = YouTubeDL(self._progress_hook, Logger.logger(__file__))

        self._sound_consumer = SoundConsumer.get_sound_consumer(self.bot, self._volume)
        self._sound_consumer_thread = threading.Thread(
            target=self._sound_consumer.run,
            name='Sound_Consumer',
            daemon=True)

        self._sound_consumer_thread.start()

    @commands.command()
    @commands.guild_only()
    @voice_command()
    async def play(self, ctx, source: str, *, times: str = '1'):
        source, times = await self._play_parse(source, times)
        if times <= 0:
            await reactions.confusion(ctx.message, delete_message_after=10)
            return

        voice = await voiceutils.join_voice_channel(ctx.message.author.voice.channel, self.bot)
        if voice is None:
            await reactions.confusion(ctx.message, delete_message_after=10)
            return

        # Keep ordering of clips
        async with self._order_lock:
            success = await self._enqueue_audio(ctx, voice, source, times)

        if not success:
            await reactions.confusion(ctx.message)

        await ctx.message.delete(delay=10)

    # Searches for a YouTube video based on the search terms given and sends the url to the play function
    @commands.command()
    @commands.guild_only()
    @voice_command()
    async def ytplay(self, ctx, *, search_terms: str):
        yt_url = ''
        if await webutils.is_link(search_terms):
            yt_url = search_terms
        else:
            results = YoutubeSearch(search_terms, max_results=20).to_dict()
            for i in range(0, len(results)):
                if results[i]['publish_time'] != 0:
                    yt_url = f"https://www.youtube.com{results[i]['url_suffix']}"
                    break
        if len(yt_url) > 0:
            await self.play(ctx, source=yt_url, times='1')
            await ctx.send(f'Added {yt_url} to the queue')
        else:
            await ctx.send('Could not find track to add')

    # 'volume' is already a superclass' method, so can't use that method name.
    @commands.command(name='volume', aliases=['vol'])
    @commands.guild_only()
    @voice_command()
    async def vol(self, ctx, volume: float):
        voice = await voiceutils.voice_in(ctx.message.author.voice.channel, self.bot)
        if voice is not None:
            self._volume = max(0.0, min(100.0, volume)) / 100.0
            if voice.is_playing():
                voice.source.volume = self._volume
        self._sound_consumer.set_volume(volume)

    @commands.command()
    @commands.guild_only()
    @voice_command()
    async def pause(self, ctx):
        voice = await voiceutils.voice_in(ctx.message.author.voice.channel, self.bot)
        if voice is not None and voice.is_playing():
            voice.pause()

    @commands.command()
    @commands.guild_only()
    @voice_command()
    async def resume(self, ctx):
        voice = await voiceutils.voice_in(ctx.message.author.voice.channel, self.bot)
        if voice is not None and voice.is_paused():
            voice.resume()

    @commands.command()
    @commands.guild_only()
    @voice_command()
    async def skip(self, ctx):
        voice = await voiceutils.voice_in(ctx.message.author.voice.channel, self.bot)
        if voice is not None and voice.is_playing():
            self._sound_consumer.skip_track()

    @commands.command(aliases=['stop'])
    @commands.guild_only()
    @voice_command()
    async def leave(self, ctx):
        voice = await voiceutils.voice_in(ctx.message.author.voice.channel, self.bot)
        if voice is not None:
            await self._quit_playing(voice)

    async def on_voice_state_update(self, _, before, after):
        if before.channel is not None and (after.channel is None or before.channel.id != after.channel.id):
            voice = await voiceutils.voice_in(before.channel, self.bot)
            # Make sure there are no humans in the voice channel
            if voice is not None and next(filter(lambda m: not m.bot, before.channel.members), None) is None:
                await self._quit_playing(voice)

    async def _quit_playing(self, voice):
        await self.loop.run_in_executor(self.THREAD_POOL, self._sound_consumer.stop_playing)
        if voice is not None:
            await voice.disconnect()
        async with self._order_lock:
            await self.loop.run_in_executor(self.THREAD_POOL, fileutils.delete_directories, self.CACHE_DIR)

    async def _enqueue_audio(self, ctx, voice, source, times):
        track = await self._create_track(ctx, voice, source, times)
        if track is None:
            return False

        self._sound_consumer.enqueue(track)

        return True

    async def _create_track(self, ctx, voice, source, times):
        is_link = await webutils.is_link(source)

        if not is_link:
            track_source = await fileutils.find_file_async(self.CLIP_DIR, source)
        else:
            track_source = await self._download_link(ctx, source)

        if track_source is None:
            return None

        return Track(ctx, voice, track_source, is_link, times)

    async def _download_link(self, ctx, link):
        file_path = os.path.join(self.CACHE_DIR, await self._link_hash(link))
        if os.path.exists(file_path):
            return file_path

        # TODO DL AND PLAY EVEN ON FAILURE
        info = await self.bot.loop.run_in_executor(self.THREAD_POOL, self._yt_downloader.info, link)

        if info is None or not all(key in info for key in ('duration', 'thumbnails', 'title', 'uploader')):
            Logger(__file__) \
                .message('Track info missing expected key(s)') \
                .add_field('info', info) \
                .error()
            return None

        self._uploader = info['uploader']
        self._title = info['title']
        self._thumbnail = info['thumbnails'][-1]['url']
        self._duration = info['duration']
        self._url = link
        self._last_embed_message = await ctx.send(embed=self._status_embed())
        await self.bot.loop.run_in_executor(self.THREAD_POOL, self._yt_downloader.download, link, file_path)

        return f'{file_path}.m4a'

    def _progress_hook(self, data):
        if data is None or self._last_embed_message is None:
            return

        # TODO THIS IS REALLY SLOW TO DISPLAY STATUS IN REAL TIME
        asyncio.run_coroutine_threadsafe(self._last_embed_message.edit(embed=self._status_embed(data)), self.bot.loop)

    def _status_embed(self, fields=None):
        if fields is None:
            progress_display = {'Progress': 'Starting...'}
        else:
            progress_display = self._progress_display(fields)

        if progress_display.get('Progress') == 'Error':
            title = 'Failed'
        else:
            title = 'Playing' if progress_display.get('Progress') == 'Finished' else 'Downloading'

        description_markdown = f'Uploader: {self._uploader}\n\n[{self._title}]({self._url})'

        embed = (Embed(title=title, description=description_markdown, color=0xFF0000)
                 .set_image(url=self._thumbnail))

        for name, value in progress_display.items():
            embed.add_field(name=name, value=value)

        if self._duration is not None and self._duration > 0 and 'Playing' in title:
            embed.add_field(name='Duration', value=self._duration)

        return embed

    @staticmethod
    def _progress_display(data):
        if 'status' not in data:
            return {'Progress': 'Starting...'}

        if data['status'] == 'error':
            return {'Progress': 'Error'}
        elif data['status'] == 'finished':
            return {'Progress': 'Playing...'}

        total_size = data.get('total_bytes')
        if total_size is None:
            total_size = data.get('total_bytes_estimate')

        if total_size is not None:
            return {'Progress': f"{int(data['downloaded_bytes'] / total_size * 100)}%"}

        return {'Progress': "Can't be determined"}

    @staticmethod
    async def _link_hash(link):
        md5hash = hashlib.new('md5')
        md5hash.update(f'sp_{link}'.encode('utf-8'))
        return md5hash.hexdigest()

    @staticmethod
    async def _play_parse(source, times):
        times_split = times.split()

        try:
            times = int(times_split[-1])
            source = f'{source} {" ".join(times_split[:-1])}'
        except ValueError:
            source = f'{source} {times}'
            times = 1

        return source, times


def setup(bot):
    bot.add_cog(SoundPlayer(bot))


def teardown(_):
    if SoundPlayer.THREAD_POOL:
        SoundPlayer.THREAD_POOL.shutdown()
    cache_path = os.path.join(EXTENSION_RESOURCES_DIR, 'music', 'cache')
    fileutils.delete_directories(cache_path, True)
