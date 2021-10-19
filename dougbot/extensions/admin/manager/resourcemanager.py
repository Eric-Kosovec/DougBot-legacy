import os
import shutil

import discord

from discord.ext import commands

from dougbot.core.bot import DougBot
from dougbot.common.long_message import long_message
from dougbot.extensions.common import webutil
from dougbot.extensions.common import fileutils
from dougbot.extensions.common.annotations.admincheck import admin_command


class ResourceManager(commands.Cog):

    # TODO MULTI-PART FILE UPLOADS/DOWNLOADS, ZIP FILES

    def __init__(self, bot: DougBot):
        self._bot = bot
        self._root = self._bot.RESOURCES_DIR
        self._path = self._root

    @commands.command()
    @admin_command()
    async def ls(self, ctx):
        files = os.listdir(self._path)
        files.sort()
        output = '\n'.join(files)
        for message in long_message(output):
            await ctx.send(message)

    @commands.command()
    @admin_command()
    async def cd(self, ctx, directory: str):
        next_path = fileutils.PathBuilder(self._path, self._root)\
            .directory_only()\
            .join(directory)\
            .build()

        if next_path is None:
            await self._bot.confusion(ctx.message)
        else:
            self._path = next_path
            await ctx.send(self._path)

    @commands.command()
    @admin_command()
    async def remove(self, ctx, filename: str, *, recursive: bool = False):
        target = fileutils.PathBuilder(self._path, self._root)\
            .join(filename)\
            .build()

        if os.path.isfile(target):
            os.remove(target)
        elif os.path.isdir(target):
            if recursive:
                shutil.rmtree(target)
            else:
                os.rmdir(target)
        else:
            await self._bot.confusion(ctx.message, f'File {target} is not a regular file or directory')
            return

        await self._bot.confirmation(ctx.message)

    @commands.command()
    @admin_command()
    async def get(self, ctx, path):
        target = fileutils.PathBuilder(self._path, self._root)\
            .join(path)\
            .build()
        if os.path.isfile(target):
            await ctx.send(file=discord.File(target))
        else:
            await self._bot.confusion(ctx.message, f'{target} is not a file')

    @commands.command()
    @admin_command()
    async def move(self, ctx, source: str, dest: str):
        source_path = fileutils.PathBuilder(self._path, self._root)\
            .join(source)\
            .build()
        dest_path = fileutils.PathBuilder(self._path, self._root) \
            .join(dest)\
            .build()
        if not os.path.exists(dest_path):
            os.makedirs(dest_path, exist_ok=True)
        os.rename(source_path, dest_path)
        await self._bot.confirmation(ctx.message)

    @commands.command()
    @admin_command()
    async def rename(self, ctx, source: str, dest: str):
        await self.move(ctx, source, dest)

    @commands.command()
    @admin_command()
    async def mkdir(self, ctx, name: str):
        os.mkdir(fileutils.PathBuilder(self._path, self._root).join(name))
        await self._bot.confirmation(ctx.message)

    @commands.command()
    @admin_command()
    async def mkfile(self, ctx, file_path: str):
        target = fileutils.PathBuilder(self._path, self._root).join(file_path)

        if len(ctx.message.attachments) <= 0:
            await self._bot.confusion(ctx.message)
            return

        url = ctx.message.attachments[0].url
        file = await webutil.download_file(url)
        with open(target, 'wb') as fd:
            shutil.copyfileobj(file.raw, fd)

        await self._bot.confirmation(ctx.message)

    @commands.command()
    @admin_command()
    async def cwd(self, ctx):
        await ctx.send(self._path)

    @commands.command()
    @admin_command()
    async def home(self, _):
        self._path = self._root


def setup(bot):
    bot.add_cog(ResourceManager(bot))
