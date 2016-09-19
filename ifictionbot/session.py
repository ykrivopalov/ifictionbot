import asyncio
import math
import os
import shelve
import sqlite3
import time

from logging import debug, info, error

import telepot
import telepot.aio

HELP_MESSAGE = """This bot allows you to play interactive fiction.

In such games, you play the role of a character in a story.  In order to move the story forward, you'll type commands that cause your character to do things. The interpreter will describe what the fictional world looks like. If your action causes a change in the world of the story, the software will usually tell you.

Note that commands can be passed only in such forms:
- VERB
- VERB NOUN
- VERB PREPOSITION NOUN
- VERB NOUN PREPOSITION NOUN
- PERSON, VERB NOUN

Examples of commands:
- "Look" - just look around (or in short form just "L")
- "Look at the hatch"
- "Open the hatch"
- "Go North" go to north direction (or in short form just "North" or "N")
- "Again" - repeat last command (or in short form just "G")
- "Wait" - wait until something happenned (or in short form just "Z")

Links:
- [What is interactive fiction](https://en.wikipedia.org/wiki/Interactive_fiction)
- [How to play interactive fiction](http://www.musicwords.net/if/how_to_play.htm)
- [How to play in one picture](http://pr-if.org/doc/play-if-card/play-if-card.html)

Bot implemented with [FrobTADS](https://github.com/realnc/frobtads) interpretator.

Please report bugs and feature requests to @yktor.
"""


def unique_list_prepend(ls, val):
    result = [val]
    result += [l for l in ls if l != val]
    return result


class GameIterator:
    def __init__(self, db, current_page, page_size, count):
        self._db = db
        self._page_size = page_size
        self._count = count
        self._current_page = current_page

    def get_page(self):
        return self._db.get_games(self._current_page * self._page_size, self._page_size)

    def get_page_number(self):
        return self._current_page

    def next(self):
        if self._current_page == self._count:
            debug("Can't iterate next")
        else:
            self._current_page += 1
        return self.get_page()

    def prev(self):
        if self._current_page == 0:
            debug("Can't iterate prev")
        else:
            self._current_page -= 1
        return self.get_page()

    def ways_to_iterate(self):
        return (self._current_page > 0, self._current_page < self._count - 1)


class GamesDB:
    def __init__(self, path):
        self._db = sqlite3.connect(path)

    def list_games(self, page, page_size):
        cur = self._db.cursor()
        count = int(next(cur.execute('SELECT count(*) FROM games'))[0])
        pages_count = math.ceil(count / page_size)
        return GameIterator(self, page, page_size, pages_count)

    def get_games(self, offset, count):
        cur = self._db.cursor()
        return cur.execute('SELECT * FROM games LIMIT ? OFFSET ?', (count, offset))

    def get_game(self, id_):
        try:
            cur = self._db.cursor()
            return next(cur.execute('SELECT * FROM games WHERE name = ?', (id_,)))
        except StopIteration:
            return None


class Frob:
    def __init__(self, chat_id, sender):
        self._chat_id = chat_id
        self._process = None
        self._sender = sender
        self._messages_to_skip = 0

    async def start(self, path, game):
        info("chat {}: frob start".format(self._chat_id))
        self._process = await asyncio.create_subprocess_shell(
            'frob -iplain {}/{}.gam'.format(path, game),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, preexec_fn=os.setpgrp)

        if os.path.exists(path + '/last.sav'):
            await self._read_output()  # just ignore all previous output
            self.restore_game('last')
        else:
            self._messages_to_skip = 1  # ignore frobTADS intro msg

    def stop(self):
        info("chat {}: frob stop".format(self._chat_id))
        if not self._process.returncode:  # process not finished yet
            self.save_game('last')
            time.sleep(1)  # TODO don't use sync wait
            self._process.terminate()

    async def _read_output(self):
        lines = [await self._process.stdout.readline()]
        try:
            while not self._process.stdout.at_eof():
                lines.append(await asyncio.wait_for(self._process.stdout.readline(), 0.1))
        except asyncio.TimeoutError:
            pass

        return lines

    async def read_loop(self):
        while not self._process.stdout.at_eof():
            lines = await self._read_output()
            msgs = self._parse_lines(lines)
            for msg in msgs:
                if self._messages_to_skip:
                    self._messages_to_skip -= 1
                    continue

                await self._sender.sendMessage(msg)

        await self._sender.sendMessage('Game closed')
        info('Frob eof reached')

    @staticmethod
    def _get_lines_delimiter(start, end):
        if not start:
            return ''

        if end[0].isupper() or end[0] == ' ':  # try delete redundant '\n'
            return '\n'
        else:
            return ' '

    def _parse_lines(self, blines):
        lines = [l.decode('utf-8') for l in blines]
        for l in lines:
            debug('frob output: %s', l)

        msgs = []
        last_msg = ''
        for b in lines:
            if b:
                if b[0] == '>':
                    b = b[1:]

                if b == '\n':  # put paragraphs in separate messages
                    if last_msg.strip():
                        msgs.append(last_msg)
                    last_msg = ''
                else:
                    last_msg += self._get_lines_delimiter(last_msg, b)
                    last_msg += b[:-1]  # without '\n'

        if last_msg.strip():
            msgs.append(last_msg)

        return msgs

    def save_game(self, name):
        info("chat {}: save game '{}'".format(self._chat_id, name))
        self._process.stdin.write(bytes('save\n', 'utf-8'))
        self._process.stdin.write(bytes(name + '\n', 'utf-8'))

    def restore_game(self, name):
        info("chat {}: restore game '{}'".format(self._chat_id, name))
        self._process.stdin.write(bytes('restore\n', 'utf-8'))
        self._process.stdin.write(bytes(name + '\n', 'utf-8'))

    def restart(self):
        info("chat {}: restart".format(self._chat_id))
        self._process.stdin.write(bytes('restart\n', 'utf-8'))
        self._process.stdin.write(bytes('y\n', 'utf-8'))

    async def command(self, cmd):
        if self._process.returncode:
            await self._sender.sendMessage('Game not started')
        else:
            info("chat {}: command '{}'".format(self._chat_id, cmd))
            self._process.stdin.write(bytes(cmd + '\n', 'utf-8'))

DIALOG_MAIN = 'main'
DIALOG_BROWSING = 'browsing'
DIALOG_LAST_PLAYED = 'last-played'
DIALOG_GAME = 'game'


class MainDialog:
    _GAMES_DB = 'Games database'
    _RECENTLY_PLAYED = 'Recently played games'
    _HOWTO = 'How to play'
    _KEYBOARD = {'keyboard': [[_GAMES_DB], [_RECENTLY_PLAYED], [_HOWTO]],
                 'resize_keyboard': True}

    def __init__(self, sender):
        self._sender = sender

    async def start(self, greetings=False):
        if greetings:
            await self._sender.sendMessage('Choose section', reply_markup=self._KEYBOARD)

    def stop(self):
        pass

    async def on_message(self, msg):
        debug('MainDialog on_message')
        content_type = telepot.glance(msg)[0]
        if content_type != 'text':
            return

        text = msg['text']
        if text == self._GAMES_DB:
            return DIALOG_BROWSING, {}
        elif text == self._RECENTLY_PLAYED:
            return DIALOG_LAST_PLAYED, {}
        elif text == self._HOWTO:
            await self._sender.sendMessage(HELP_MESSAGE, parse_mode='Markdown')
        else:
            await self._sender.sendMessage('Choose section', reply_markup=self._KEYBOARD)

        return DIALOG_MAIN, {}


class BrowsingDialog:
    _DEFAULT_STATE = {'page': 0}

    _BACKWARD = '⬅️ Backward'
    _FORWARD = 'Forward ➡️'
    _CANCEL = 'Return to the main menu'

    def __init__(self, state, sender, games_db):
        self._state = state
        if not self._state:
            self._state.update(self._DEFAULT_STATE)

        self._games_db = games_db
        self._sender = sender
        self._iterator = self._games_db.list_games(self._state['page'], 3)

    def _make_keyboard(self):
        to_left, to_right = self._iterator.ways_to_iterate()
        if to_left and to_right:
            browsing_keys = [self._BACKWARD, self._FORWARD]
        elif not to_left and to_right:
            browsing_keys = [self._FORWARD]
        elif to_left and not to_right:
            browsing_keys = [self._BACKWARD]
        else:
            browsing_keys = []

        return {'keyboard': [browsing_keys, [self._CANCEL]],
                'resize_keyboard': True}

    @staticmethod
    def _make_items_list(items):
        result = '\n'.join(('/{} - {}'.format(name, desc) for name, desc in items))
        return result if result else 'Empty'

    async def start(self, greetings=False):
        if greetings:
            await self._sender.sendMessage('Here you can see TADS games from ifarchive.org')

            msg = self._make_items_list(self._iterator.get_page())
            await self._sender.sendMessage(msg, reply_markup=self._make_keyboard())

    def stop(self):
        self._state['page'] = self._iterator.get_page_number()

    async def on_message(self, msg):
        debug('BrowsingDialog on_message %s', msg)
        content_type = telepot.glance(msg)[0]
        if content_type != 'text':
            return DIALOG_BROWSING, {}

        text = msg['text']
        if text == self._FORWARD:
            items = self._iterator.next()
        elif text == self._BACKWARD:
            items = self._iterator.prev()
        elif text == self._CANCEL:
            return DIALOG_MAIN, {}
        elif text.startswith('/'):
            return DIALOG_GAME, {'game': text[1:]}
        else:
            items = self._iterator.get_page()

        msg = self._make_items_list(items)
        await self._sender.sendMessage(msg, reply_markup=self._make_keyboard())
        return DIALOG_BROWSING, {}


class LastPlayedDialog:
    _CANCEL = 'Return to the main menu'
    _KEYBOARD = {'keyboard': [[_CANCEL]], 'resize_keyboard': True}

    def __init__(self, state, sender, games_db):
        self._state = state
        self._sender = sender
        self._games_db = games_db

    async def start(self, greetings=False):
        debug('LastPlayedDialog start %s', greetings)
        if greetings:
            await self._send_last_played_games()

    async def _send_last_played_games(self):
        debug('last played %s', self._state)
        msg_lines = ['Recently played games:']
        for g in self._state['games']:
            debug('get game %s', self._games_db.get_game(g))
            game = self._games_db.get_game(g)
            if game:
                msg_lines.append('/{} - {}'.format(*game))
            else:
                msg_lines.append('{} - game no more accessible'.format(g))

        debug('send msg %s', msg_lines)
        await self._sender.sendMessage('\n'.join(msg_lines), reply_markup=self._KEYBOARD)

    def stop(self):
        pass

    async def on_message(self, msg):
        content_type = telepot.glance(msg)[0]
        if content_type != 'text':
            return

        text = msg['text']
        debug('LastPlayedDialog on_message %s', text)
        if text == self._CANCEL:
            return DIALOG_MAIN, {}
        elif text.startswith('/'):
            return DIALOG_GAME, {'game': text[1:]}
        else:
            await self._send_last_played_games()
            return DIALOG_LAST_PLAYED, {}


def init_user_dir(data_path, user_id):
    user_path = os.path.abspath('{}/users/{}'.format(data_path, user_id))
    if not os.path.exists(user_path):
        os.makedirs(user_path)


def init_game_dir(data_path, user_id, game):
    source_game_path = os.path.abspath('{}/games/{}.gam'.format(data_path, game))
    game_data_path = os.path.abspath('{}/users/{}/{}'.format(data_path, user_id, game))
    if not os.path.exists(game_data_path):
        os.makedirs(game_data_path)
    game_path = '{}/{}.gam'.format(game_data_path, game)
    os.system('ln -sf {} {}'.format(source_game_path, game_path))
    return game_data_path


class SenderWithKeyboard:
    def __init__(self, sender, keyboard):
        self._sender = sender
        self._keyboard = keyboard

    async def sendMessage(self, msg):
        return await self._sender.sendMessage(msg, reply_markup=self._keyboard)

class GameDialog:
    _RETURN = 'Return to the main menu'
    _KEYBOARD = {'keyboard': [['Status', 'Undo', 'Restart'], [_RETURN]],
                 'resize_keyboard': True}

    def __init__(self, state, last_played, loop, chat_id, sender, data_path, games_db):
        self._state = state
        self._last_played = last_played
        self._loop = loop
        self._chat_id = chat_id
        self._sender = sender
        self._data_path = data_path
        self._games_db = games_db
        self._game = None
        self._read_loop_task = None

    async def start(self, game=None, greetings=False):
        if game:
            self._state['game'] = game
        else:
            game = self._state['game']

        self._last_played['games'] = unique_list_prepend(self._last_played['games'], game)[:10]

        sender = SenderWithKeyboard(self._sender, self._KEYBOARD)
        self._game = Frob(self._chat_id, sender)
        data_path = init_game_dir(self._data_path, self._chat_id, game)
        await self._game.start(data_path, game)
        self._read_loop_task = self._loop.create_task(self._game.read_loop())
        if greetings:
            await self._sender.sendMessage(
                'Starting "{}" game'.format(game), reply_markup=self._KEYBOARD)

    def stop(self):
        debug('stop game dialog')
        if self._game:
            self._game.stop()
            self._read_loop_task.cancel()
            self._game = None

    async def on_message(self, msg):
        debug('GameDialog on_message')
        content_type = telepot.glance(msg)[0]
        if content_type != 'text':
            return

        text = msg['text']
        if text.startswith('/command'):
            text = text[9:]
        elif text.startswith('/c'):
            text = text[3:]

        if text.lower() in ['save', 'restore', 'quit', 'q']:
            await self._sender.sendMessage('This command currently unsupported',
                                           reply_markup=self._KEYBOARD)
            return DIALOG_GAME, {}
        elif text == self._RETURN:
            return DIALOG_MAIN, {}
        elif not text:
            return DIALOG_GAME, {}
        else:
            await self._game.command(text)
            return DIALOG_GAME, {}


class UserDB:
    def __init__(self, data_path, user_id, init_state):
        self._db = shelve.open('{}/users/{}/user.shlv'.format(data_path, user_id))
        self._user_id = user_id

        for key, value in init_state.items():
            if key not in self._db:
                self._db[key] = value

        self._db.sync()

    def current_state(self):
        return dict(self._db)

    def save_state(self, state):
        for key, value in state.items():
            self._db[key] = value

        self._db.sync()

    def close(self):
        self._db.close()


class SessionRegistry:
    def __init__(self):
        self._sessions = {}

    def register(self, chat_id, session):
        info('chat {}: session register'.format(chat_id))
        self._sessions[chat_id] = session

    def unregister(self, chat_id):
        info('chat {}: session unregister'.format(chat_id))
        del self._sessions[chat_id]

    def close_all(self):
        closing_sessions = dict(self._sessions)
        for session in closing_sessions.values():
            session.close()


def add_to_recently_played(arr, val):
    if val in arr:
        arr.remove(val)

    arr.insert(0, val)


class Session(telepot.aio.helper.ChatHandler):
    _DEFAULT_STATE = {'current': DIALOG_MAIN,
                      'recently_played': [],
                      DIALOG_MAIN: {},
                      DIALOG_GAME: {},
                      DIALOG_LAST_PLAYED: {'games': []},
                      DIALOG_BROWSING: {}}

    def __init__(self, seed_tuple, data_path, loop, registry, **kwargs):
        super(Session, self).__init__(seed_tuple, **kwargs)
        self._chat_id = seed_tuple[1]['chat']['id']
        init_user_dir(data_path, self._chat_id)
        self._user_db = UserDB(data_path, self._chat_id, self._DEFAULT_STATE)
        games_db = GamesDB(data_path + '/games/ifarchive.db')
        self._state = self._user_db.current_state()
        self._dialogs = {
            DIALOG_MAIN: MainDialog(self.sender),
            DIALOG_BROWSING: BrowsingDialog(
                self._state[DIALOG_BROWSING], self.sender, games_db),
            DIALOG_LAST_PLAYED: LastPlayedDialog(
                self._state[DIALOG_LAST_PLAYED], self.sender, games_db),
            DIALOG_GAME: GameDialog(
                self._state[DIALOG_GAME], self._state[DIALOG_LAST_PLAYED], loop,
                self._chat_id, self.sender, data_path, games_db)
        }
        self._registry = registry

    async def open(self, msg, dummy_seed):
        try:
            info('chat {}: open'.format(self._chat_id))
            self._registry.register(self._chat_id, self)

            content_type = telepot.glance(msg)[0]
            if content_type == 'text' and msg['text'] == '/start':
                self._state['current'] = DIALOG_MAIN

            await self._dialogs[self._state['current']].start()
            return False  # process initial message
        except Exception as e:
            error('chat {}: open error {}'.format(self._chat_id, e))
            raise

    async def on_chat_message(self, msg):
        try:
            content_type = telepot.glance(msg)[0]

            if content_type != 'text':
                return

            text = msg['text']
            debug('chat {}: recv from user "{}"'.format(self._chat_id, text))
            if text == '/help':
                await self.sender.sendMessage(HELP_MESSAGE, parse_mode='Markdown')
            elif text.startswith('/game'):
                game = text[6:]
                if not game:
                    await self.sender.sendMessage(
                        'Please spicify valid game name. Your can find it through game browser')
                else:
                    await self._apply_state(DIALOG_GAME, {'game': game})
            elif text.startswith('/command') or text.startswith('/c'):
                if self._state['current'] == DIALOG_GAME:
                    await self._pass_message(msg)
                else:
                    await self.sender.sendMessage('This command works only when game opened')
            else:
                await self._pass_message(msg)

        except Exception as e:
            error('chat {}: on_message error {}: {}'.format(self._chat_id, msg, e))
            raise

    async def _pass_message(self, msg):
        new_state, args = await self._dialogs[self._state['current']].on_message(msg)
        if new_state != self._state['current']:
            await self._apply_state(new_state, args)

    async def _apply_state(self, state, args):
        if state == DIALOG_GAME:
            add_to_recently_played(self._state['recently_played'], args['game'])

        self._dialogs[self._state['current']].stop()
        self._state['current'] = state
        await self._dialogs[self._state['current']].start(**args, greetings=True)

    async def on__idle(self, event):
        info('chat {}: on__idle {}'.format(self._chat_id, event))
        self.close()

    def close(self):
        info('chat {}: close'.format(self._chat_id))
        for d in self._dialogs.values():
            d.stop()
        self._user_db.save_state(self._state)
        self._user_db.close()
        self._registry.unregister(self._chat_id)
