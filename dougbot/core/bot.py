import asyncio
import inspect
import logging
import os
import sys
import traceback

import discord
from discord.ext import commands
from discord.utils import find

from dougbot.common.kvstore import KVStore
from dougbot.core.config import Config
from dougbot.common.database import Database
from dougbot.core.extloader import ExtensionLoader
from dougbot.core.util.channelhandler import ChannelHandler

# https://discordpy.readthedocs.io/


class DougBot(commands.Bot):
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    RESOURCES_DIR = os.path.join(ROOT_DIR, 'resources')

    START_MESSAGE = "\nI'm starting..."
    ENDING_MESSAGE = "\nI'm dying..."
    END_MESSAGE = "\nI've perished."

    def __init__(self, token_file, bot_config, server_config):
        self._config = Config(token_file, bot_config, server_config)
        self._dougdb = Database(os.path.join(self.RESOURCES_DIR, 'core', 'db', 'dougbot.db'))  # For core bot settings

        # For notifying text channels the bot is online. Used to prevent spamming in case of shaky
        # internet, as on_ready can be called multiple times in such a case.
        self._on_ready_called = False
        self._log_channel = None
        self._appinfo = None

        intent = discord.Intents.default()
        intent.members = True
        intent.presences = True

        super().__init__(self._config.command_prefix, intents=intent, case_insensitive=True)
        self._extension_load_errors = ExtensionLoader.load_extensions(self)

    def run(self, *args, **kwargs):
        print(self.START_MESSAGE)
        try:
            super().run(*(self._config.token, *args), **kwargs)
        except Exception as e:
            print(f'\nFATAL EXCEPTION: Uncaught exception while running bot: {e}', file=sys.stderr)
            traceback.print_exc()
        finally:
            print(self.ENDING_MESSAGE)
            if self.loop is not None and not self.loop.is_closed():
                asyncio.run_coroutine_threadsafe(self.logout(), self.loop)
            print(self.END_MESSAGE)

    async def on_ready(self):
        if not self._on_ready_called:
            print('\nDoug Online')
            print(f'Name: {self.user.name}')
            print(f'ID: {self.user.id}')
            print('-' * (len(str(self.user.id)) + 4))

            self._log_channel = self.get_channel(self._config.logging_channel_id)
            if self._log_channel is not None:
                self._init_logging(self._log_channel)
                await self._log_channel.send('I am sad.')

            self._on_ready_called = True
            self._appinfo = await self.application_info()

            for exception in self._extension_load_errors:
                logging.getLogger(__file__).log(logging.ERROR, f'{exception}\n{traceback.format_tb(exception.original.__traceback__)}')

    async def on_command_error(self, ctx, error):
        error_texts = {
            commands.errors.MissingRequiredArgument: f'Missing argument(s), type {self._config.command_prefix}help <command_name>',
            commands.errors.CheckFailure: f'{ctx.author.mention} You do not have permissions for this command.',
            commands.errors.NoPrivateMessage: 'Command cannot be used in private messages.',
            commands.errors.DisabledCommand: 'Command disabled and cannot be used.',
            commands.errors.CommandNotFound: 'Command not found.',
            commands.errors.CommandOnCooldown: 'Command on cooldown.'
        }

        for error_class, error_msg in error_texts.items():
            if isinstance(error, error_class):
                await self.confusion(ctx.message, error_msg)
                return

        # Catches rest of exceptions
        logging.getLogger(__file__).log(logging.ERROR, f'{error}\n{traceback.format_tb(error.original.__traceback__)}')
        await self.check_log(ctx.message)

    @staticmethod
    async def check_log(message, error_msg=None):
        if message is not None:
            page_emoji = '\U0001F4C4'
            await message.add_reaction(page_emoji)
            if error_msg is not None:
                await message.channel.send(error_msg)

    @staticmethod
    async def confusion(message, error_msg=None):
        if message is not None:
            question_emoji = '\U00002753'
            await message.add_reaction(question_emoji)
            if error_msg is not None:
                await message.channel.send(error_msg)

    @staticmethod
    async def confirmation(message, confirm_msg=None):
        if message is not None:
            ok_hand_emoji = '\U0001F44C'
            await message.add_reaction(ok_hand_emoji)
            if confirm_msg is not None:
                await message.channel.send(confirm_msg)

    # Sibling module is a python file within the same package as the caller, unless caller is a core or admin module.
    def kv_store(self, sibling_module=None):
        caller_stack = inspect.stack()[1]
        calling_module = inspect.getmodule(caller_stack[0]).__name__
        # dougbot.package...package...module

        if sibling_module is not None:
            sibling_module = sibling_module.replace(os.sep, '.')
            extension_package = 'dougbot.extensions'
            if not sibling_module.startswith(extension_package):
                sibling_module = f'{extension_package}.{sibling_module}'

            if not self._is_admin_package(calling_module) and not self._same_extension_package(calling_module, sibling_module):
                raise ValueError(f"Cannot get sibling module '{sibling_module}' from '{calling_module}'")

        return KVStore(self._dougdb, calling_module.replace('.', '_'))

    def package_resource_path(self):
        caller_stack = inspect.stack()[1]
        calling_module = inspect.getmodule(caller_stack[0]).__name__

        if not calling_module.startswith('dougbot.'):
            return self.RESOURCES_DIR

        components = calling_module.split('.')

        if components[1] == 'core':
            return os.path.join(self.RESOURCES_DIR, 'core')
        elif components[1] == 'extensions':
            return os.path.join(self.RESOURCES_DIR, os.sep.join(components[1: -1]))

        return self.RESOURCES_DIR

    async def join_voice_channel(self, channel):
        if channel is not None:
            vc = await self.get_voice(channel)
            return vc if vc is not None else await channel.connect()
        return None

    @staticmethod
    async def leave_voice_channel(voice):
        if voice is not None:
            await voice.disconnect()

    async def in_voice_channel(self, channel):
        return await self.get_voice(channel) is not None

    async def get_voice(self, channel):
        if channel is not None:
            return find(lambda vc: vc.channel.id == channel.id, self.voice_clients)
        return None

    async def log_channel(self):
        return self._log_channel

    def get_config(self):
        return self._config

    def owner_id(self):
        return self._appinfo.owner.id

    ''' PRIVATE METHODS '''

    def _init_logging(self, channel):
        # Add the custom handler to the root logger, so it applies to every time logging is called.
        logging.getLogger('').addHandler(ChannelHandler(self.ROOT_DIR, channel, self.loop))

    @staticmethod
    def _same_extension_package(main_module: str, sibling_module: str):
        extension_package = 'dougbot.extension'
        i = 0
        if main_module.startswith(extension_package):
            i = len(extension_package) + 1

        j = 0
        if sibling_module.startswith(extension_package):
            j = len(extension_package) + 1

        while i < len(main_module) and j < len(sibling_module):
            if main_module[i] != sibling_module[j]:
                return False
            if main_module[i] == sibling_module[j] == '.':
                return True
            i += 1
            j += 1

        return False

    @staticmethod
    def _is_admin_package(module):
        return module.startswith(f'dougbot.core') or module.startswith(f'dougbot.extensions.admin')


if __name__ == '__main__':
    dougbot = DougBot('../../config/token', '../../config/bot_config.ini', '../../config/server_config.ini')
    dougbot.run()
