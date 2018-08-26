import json
import time
import asyncio
import logging
import os.path
import threading
from discord import Game
from discord import utils
from discord import Embed
from discord.ext.commands import Bot

#Sets up logging. Template taken from 
# discordpy.readthedocs.io/en/latest/logging.html
logger = logging.getLogger('discord')
logger.setLevel(logging.INFO)
handler = logging.FileHandler(filename='discord.log'
                            , encoding='ascii', mode='w')
handler.setFormatter(
        logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

#------------SETTUP------------#

bot = Bot(command_prefix='~', case_insensitve=True)
server_configs = dict()
global_member_times = dict()
role_orders = dict()
active_threads = []
lock = threading.Lock()
#active_threads prevents creation of threads due to muting and deafening events
bot.remove_command('help')
with open('config.json', 'r') as file:
    config = json.load(file)

#------------EVENTS------------#

@bot.event
async def on_ready():
    for server in bot.servers:
        await bot.on_server_join(server)
    await bot.change_presence(game=Game(name='by itself'))
    logger.info(str(server_configs))

@bot.event
async def on_server_join(server):
    stats_start(server)
    config_start(server)
    role_orders.update({server.id:get_roles_in_order(server)})

#let admins decide how long until thrown in afk.

@bot.event
async def on_voice_state_update(before, after):
    if after.voice.voice_channel is not None:
        if after.id not in active_threads:
            active_threads.append(after.id)
            TimeTracker(after.server, after).start()
    elif after.voice.voice_channel is None:
        active_threads.remove(before.id)

#------------COMMANDS------------#

@bot.command(pass_context=True)
async def help(context):
    embeder = Embed(title='List of Commands', colour=26574, type='rich')
    embeder.add_field(name='~settup', value='Displays ' 
                        + 'settup for ranks and commands to change them')
    embeder.add_field(name='~whitelist [discord_name#XXXX]', value='Adds '
                        + 'people to the whitelist. People on the whitelist '
                        + 'are not ranked by time. Typing "~whitelist" by '
                        + 'itself lists people on the list.')
    embeder.add_field(name='~unwhitelist [discord_name#XXXX]', value='Removes '
                        + 'people from the whitelist. People removed will have '
                        + 'their ranks set to the lowest level.')
    embeder.add_field(name='~update', value='Force update ranks.')
    await bot.send_message(context.message.channel, embed=embeder)
    logger.debug(embeder.fields)

@bot.command(pass_context=True)
async def settup(context):
    if not os.path.isfile(context.message.server.id + '.txt'):
        config = open(context.message.server.id + '.txt', 'w')
        server_configs.update({context.message.server.id: dict()})
    else:
        config = open(context.message.server.id + '.txt', 'r')
    embeder = Embed(title='Rank Settup', colour=16777215, type='rich')

    await bot.send_message(context.message.channel, embed=embeder)
    config.close()
    logger.debug(embeder.fields)

#time will be in hours, decimals allowed

@bot.command(name='ranktime', pass_context=True)
async def rank_time(context, rank, time):
    count = 0
    for role in context.message.server.roles:
        if role.name == rank:
            count += 1
            if(count > 1):
                await bot.say('Cannot change rank time if multiple ranks have the '
                        + 'same name.')
                return
    if count == 0:
        await bot.say('Cannot find a rank with the name %s.' % rank)
    if not is_pos_number(time):
        await bot.say('The time field must be a number and positive')
        return
    else:
        server_id = context.message.server.id
        change_config(server_id, rank, str(time))
        role_orders.update({server_id:get_roles_in_order(context.message.server)})
        

#create check for number of arguments, and time must be greater than ?
#create checks for type of arguments.
#restriction on amount of time.
#remember afks
#------------CHECKS------------#

#------------HELPER METHODS------------#

def stats_start(server):
    if not os.path.isfile(server.id + 'stats.txt'):
        curr_stats = open(server.id + 'stats.txt', 'w')
        global_member_times.update({server.id:dict()})
        for member in server.members:
            global_member_times[server.id].update({member.id:[0, 0]})
    elif os.stat(server.id + 'stats.txt').st_size == 0:
        global_member_times.update({server.id:dict()})
        for member in server.members:
            global_member_times[server.id].update({member.id:[0, 0]})
        return
    else:
        member_times = dict()
        curr_stats = open(server.id + 'stats.txt', 'r')
        readable = curr_stats.read().split(';')
        for pair in readable:
            member_id, time_and_rank = pair.split('=')
            time_and_rank = time_and_rank.strip('[\n]')
            time_and_rank = time_and_rank.split(', ')
            time_and_rank[0] = int(time_and_rank[0])
            time_and_rank[1] = int(time_and_rank[1])
            member_times.update({member_id:time_and_rank})
        global_member_times.update({server.id:member_times})
    curr_stats.close()

#the second element in the lists will be rank positions in the list, not name

def config_start(server):
    if not os.path.isfile(server.id + '.txt'):
        curr_config = open(server.id + '.txt', 'w+')
        settings = dict()
    elif os.stat(server.id + '.txt').st_size == 0:
        server_configs.update({server.id: dict()})
        return
    else:
        curr_config = open(server.id + '.txt', 'r')
        readable = curr_config.read()
        settings = dict(pair.split('=') for pair in readable.split(';'))
    server_configs.update({server.id: settings})
    curr_config.close()


def change_config(server_id, option, value):
    settings = server_configs[server_id]
    settings[option] = value
    new_config = open(server_id + '.txt', 'w+')
    iterator = iter(settings)
    first_key = next(iterator)
    new_config.write(first_key + '=' + str(settings[first_key]))
    for key in iterator:
        new_config.write(';' + key + '=' + str(settings[key]))
    new_config.close()

def convert_time(time):
    value = float(time)
    return int(value * 60 * 60)

def get_roles_in_order(server):
    to_sort = dict()
    for role in server.roles:
        try:
            to_sort.update({role.name:convert_time(
                    server_configs[server.id][role.name])})
        except KeyError as e:
            continue
    return sorted(to_sort, key=to_sort.get)

def is_pos_number(time):
    try:
        number = float(time)
    except ValueError as e:
        return False
    if number < 0:
        return False
    return True


#account for server admins manually asigning roles (even by accident)
#if new roles added later, be sure to allow for update
#allow admins to turn message feature off
#sleep thread if too straining on cpu
#account for change in time while people are in channel

#------------THREADING CLASSES------------#

class TimeTracker(threading.Thread):

    def __init__(self, server, member):
        super().__init__(daemon=True)
        times = global_member_times[server.id]
        self.server = server
        self.member = member
        self.member_time = times[member.id][0]
        try:
            self.next_rank = role_orders[server.id][times[member.id][1]]
            self.rank_time = convert_time(server_configs[server.id][self.next_rank])
        except IndexError as e:
            self.rank_time = None

    def run(self):
        now = time.time()
        times = global_member_times[self.server.id]
        rank_time = self.rank_time + now
        while self.member.voice.voice_channel is not None:
            times[self.member.id][0] = self.member_time + time.time() - now
            if (self.rank_time is not None and 
                    self.member_time + time.time() >= rank_time):
                future = asyncio.run_coroutine_threadsafe(
                        bot.replace_roles(self.member, utils.find(
                        lambda role: role.name == self.next_rank
                        , self.server.roles)), bot.loop)
                future.result()
                times[self.member.id][1] += 1
                reciever = self.server.default_channel
                #let server admins decide on the message? Placeholder.
                fmt_tup = (self.member.nick, "to_format_later",
                                self.next_rank)
                message = ("Congratulations %s! You've spent a total of "
                            + "in %s's voice channels and have therefore "
                            + "earned the rank of %s! Go wild~") % fmt_tup
                if reciever is not None and reciever.type == ChannelType.text:
                    future = asyncio.run_coroutine_threadsafe(
                            bot.send_message(reciever, message), bot.loop)
                else:
                    future = asyncio.run_coroutine_threadsafe(
                            bot.send_message(self.member, message), bot.loop)
                future.result()
                try:
                    self.next_rank = role_orders[self.server.id][times[self.member.id][1]]
                    self.rank_time = convert_time(server_configs[self.server.id][self.next_rank])
                    rank_time = self.rank_time + now
                except IndexError as e:
                    self.rank_time = None



class PeriodicUpdater(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True)

    def run(self):
        while True:
            for server in bot.servers:
                with open(server.id + 'stats.txt', 'w') as stats:
                    server_times = global_member_times[server.id]
                    iterator = iter(server_times)
                    first_key = next(iterator)
                    stats.write(first_key + '=' + str(server_times[first_key]))
                    for key in iterator:
                        stats.write(';' + key + '=' + str(server_times[key]))
            time.sleep(config['sleep_time'])

PeriodicUpdater().start()
bot.run(config['token'])
