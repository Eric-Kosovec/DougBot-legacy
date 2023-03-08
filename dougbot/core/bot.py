import sys
from typing import Any

import nextcord
from nextcord import Intents
from nextcord import Status
from nextcord.ext import commands

from dougbot import config
from dougbot.common.logger import Logger
from dougbot.common.messaging import reactions
from dougbot.core import extloader, tasks
from dougbot.core.help import CustomHelpCommand
from dougbot.core.log.channelhandler import ChannelHandler


class DougBot(commands.Bot):

    def __init__(self):
        self.config = config.get_configuration()
        self._log_channel = None

        bot_kwargs = {
            "intents": Intents.all(),
            "case_insensitive": True,
            "strip_after_prefix": True
        }

        super().__init__(self.config.command_prefix, **bot_kwargs)
        self._extension_load_errors = extloader.load_extensions(self)
        Logger.clear_handlers()  # Temporary until Supabase's realtime dependency gets rid of their global logging setup

    def run(self, *args, **kwargs):
        try:
            if not self.config.token:
                print('Token was none; check your environment variables', file=sys.stderr)
                sys.exit(1)

            print("I'm starting...")
            super().run(*(self.config.token, *args), **kwargs)
        except Exception as e:
            Logger(__file__) \
                .message('Uncaught exception') \
                .exception(e) \
                .fatal()
            sys.exit(1)

    async def on_connect(self):
        self._log_channel = await self.fetch_channel(self.config.logging_channel_id)
        if self._log_channel:
            Logger.add_handler(ChannelHandler(config.ROOT_DIR, self._log_channel, self.loop))

        self.help_command = CustomHelpCommand(dm_help=None, no_category='Misc')

        print('Doug Online')

        await tasks.start_tasks()

    async def on_ready(self):
        # Log any errors that occurred while bot was down
        if self._log_channel:
            Logger.log_fatal_file()

        for error in self._extension_load_errors:
            Logger(__file__) \
                .message('Error while loading extension') \
                .exception(error) \
                .error(to_console=True)

        self._extension_load_errors.clear()

    async def close(self):
        await self.change_presence(status=Status.offline)
        await super().close()

    async def on_error(self, event_method, *args, **kwargs):
        _, exception, _ = sys.exc_info()
        Logger(__file__) \
            .method(event_method) \
            .add_field('arguments', args) \
            .add_field('keyword_arguments', kwargs) \
            .exception(exception) \
            .error()

    async def on_command_error(self, ctx, error):
        error_texts = {
            commands.errors.CheckFailure: f'{ctx.author.mention} You do not have permissions for this command',
            commands.errors.CommandNotFound: 'Command not found',
            commands.errors.CommandOnCooldown: 'Command on cooldown',
            commands.errors.DisabledCommand: 'Command disabled',
            commands.errors.MissingRequiredArgument: f'Missing argument(s), type {ctx.prefix}help <command_name>',
            commands.errors.NoPrivateMessage: 'Command cannot be used in private messages',
            commands.errors.TooManyArguments: 'Too many arguments'
        }

        if type(error) in error_texts:
            await reactions.confusion(ctx.message, error_texts[type(error)], delete_response_after=10)
            return

        Logger(__file__) \
            .message('Error executing command') \
            .context(ctx) \
            .exception(error) \
            .error()

        await reactions.check_log(ctx.message)

    def get_cog(self, name: str) -> Any:
        """
        Override commands.Bot's get_cog to eliminate dumb warnings when type hinting the return
        :param name: Class name of Cog
        :return: Cog instance
        """
        return super().get_cog(name)

    async def join_voice_channel(self, channel):
        voice_client = await self.voice_in(channel)
        return await channel.connect() if voice_client is None else voice_client

    async def voice_in(self, channel):
        return nextcord.utils.find(lambda vc: vc.channel.id == channel.id, self.voice_clients)
