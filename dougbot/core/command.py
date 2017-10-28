import asyncio
import inspect
import re
from enum import Enum

import dougbot.core.argument
from dougbot.core.argument import ArgumentSet


class CommandLevel(Enum):
    OWNER = 5
    BLOCKED = 4
    ADMIN = 3
    USER = 2
    NULL = 1


_ARG_REGEX = r'<[a-zA-Z]+[0-9]*([_]*[a-zA-Z]*[0-9]*)*:(str|int|float|bool)(...)?>'
_ALIAS_REGEX = r'[a-zA-Z]+[0-9]*'
_argument_match = re.compile(_ARG_REGEX)
_alias_match = re.compile(_ALIAS_REGEX)


class CommandError(Exception):
    def __init__(self, msg):
        self.msg = msg


class Command:
    _DEFAULT_LEVEL = CommandLevel.USER

    def __init__(self, plugin, func, *args, **kwargs):
        self.plugin = plugin
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.level = CommandLevel.NULL
        self.regex = None
        self.aliases = []
        self.argset = None

        self._set_level(**kwargs)

        # Parse arguments, kwargs
        self._parse_aliases(*args)
        self._parse_arguments(*args)
        self.get_regex()
        self.command_matcher = re.compile(self.get_regex())

    def get_regex(self):
        if self.regex is not None:
            return self.regex

        self.regex = r'('
        or_expr = r''
        for alias in self.aliases:
            self.regex += or_expr + alias
            or_expr = r'|'
        argset_regex = self.argset.get_regex()
        self.regex += r')'
        if argset_regex and len(argset_regex) > 0:
            self.regex += dougbot.core.argument.WHITESPACE_MATCH + self.argset.get_regex()

        return self.regex

    async def execute(self, event):
        # Grab each argument and send it to the function
        sig = inspect.signature(self.func)

        args = ()
        first = True
        for parameter in sig.parameters.keys():
            try:
                if first:
                    first = False
                    continue
                args = (*args, event.args[parameter])
            except KeyError as e:
                print('Error in getting parameter from parsed arguments: %s' % e)
                # TODO

        await self.func(event, *args)

    def _parse_arguments(self, *args):
        arguments = []
        for arg in args:
            if _argument_match.match(arg) is not None:
                arguments.append(arg)
        self.argset = ArgumentSet(arguments)

    def _parse_aliases(self, *args):
        for arg in args:
            # If not an argument, must be an alias
            if _alias_match.match(arg) is not None:
                self.aliases.append(arg)

    def _set_level(self, **kwargs):
        if 'level' in kwargs.keys():
            try:
                self.level = CommandLevel[kwargs['level'].upper()]
            except KeyError:
                # TODO
                pass
        else:
            self.level = self._DEFAULT_LEVEL

    def __str__(self):
        as_str = 'Plugin: ' + str(self.plugin) + '\n'
        as_str += 'Function: ' + str(self.func) + '\n'
        as_str += 'Args: '
        for arg in self.args:
            as_str += str(arg) + ' '
        as_str += '\nKwargs: '
        for tupl in self.kwargs.items():
            as_str += str(tupl) + ' '
        return as_str


class CommandEvent:
    def __init__(self, command, message, content, match, bot):
        self.command = command
        self.message = message
        self.content = content
        self.match = match
        self.bot = bot
        self.args = command.argset.parse(content)

    def author(self):
        return self.message.author

    def bot(self):
        return self.bot

    def channel(self):
        return self.message.channel

    def server(self):
        return self.message.server

    async def reply(self, content=None, **kwargs):
        await self.send_message(self.message.channel, content, **kwargs)

    async def send_message(self, destination, content=None, **kwargs):
        await self.bot.send_message(destination, content, **kwargs)
