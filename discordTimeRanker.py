import json
import time
import asyncio
import logging
import os.path
import threading
from discord import Game
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

#if they set themselves to afk, not necessarily in afk channel, stop counting.
#maybe want to account for mute and deafened

@bot.event
async def on_voice_state_update(before, after):
    server_times = global_member_times[before.server.id]
    member_time = server_times[before.id]
    if before.voice.voice_channel not == None and member_time == 0:
        server_times[before.id] = time.time()
    elif before.voice.voice_channel == None:
        #To implement later. create update function that takes one person
        #then overload with no people. The one with no people will just call
        #the function with the parameter and will also be the user command.
        await before.update()

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

#use time.time, then, in for loop, set delay to check, time.time - initial time.time

@bot.command(pass_context=True)
async def settup(context):
    if not os.path.isfile(context.message.server.id + '.txt'):
        config = open(context.message.server.id + '.txt', 'w')
        server_configs.update({context.message.server.id: dict()})
    else:
        config = open(context.message.server.id + '.txt', 'r')
    embeder = Embed(title='Rank Settup', colour=16777215, type='rich')
    embeder.add_field(name='~frequency m/h/d [amount]', value='How often '
                        + 'ranks will automatically update.\nm/h/d denote ' 
                        + 'how you want time to be measured.\nm - minutes\n'
                        + 'h - hours\nd - days\n')
    await bot.send_message(context.message.channel, embed=embeder)
    config.close()
    logger.debug(embeder.fields)

@bot.command(pass_context=True)
async def frequency(context, measurement, time):
    server_id = context.message.server.id
    change_config(server_id, 'frequency', measurement + str(time))

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
    else:
        server_id = context.message.server.id
        change_config(server_id, rank, str(time))

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
            global_member_times[server.id].update({member.id:0})
    elif os.stat(server.id + 'stat.txt').st_size == 0:
        global_member_times.update({server.id:dict()})
        for member in server.members:
            global_member_times[server.id].update({member.id:0})
        return
    else:
        curr_stats = open(server.id + 'stats.txt', 'r')
        readable = curr_stats.read()
        member_times = dict(pair.split('=') for pair in readable.split(';'))
        global_member_times.update({server.id:member_times})
    curr_stats.close()

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
    measurement = time[0].lower()
    value = int(time[1:])
    if measurement == 'm':
        return value * 60
    elif measurement == 'h':
        return value * 60 * 60
    elif measurement == 'd':
        return value + 24 * 60 * 60

bot.run(config['token'])
