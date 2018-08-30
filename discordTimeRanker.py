import json
import time
import copy
import asyncio
import logging
import os.path
import discord
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
active_threads = dict()
#active_threads prevents creation of threads due to muting and deafening events
stat_event = threading.Event()
stat_event.set()
bot.remove_command('help')
with open('config.json', 'r') as file:
    config = json.load(file)

#------------EVENTS------------#

@bot.event
async def on_ready():
    for server in bot.servers:
        await bot.on_server_join(server)
    PeriodicUpdater().start()
    await bot.change_presence(game=Game(name='by itself'))
    logger.info(str(server_configs))

@bot.event
async def on_server_join(server):
    stats_start(server)
    config_start(server)
    role_orders.update({server.id:get_roles_in_order(server)})

@bot.event
async def on_voice_state_update(before, after):
    if after.voice.voice_channel is not None and not after.voice.is_afk:
        if after.id not in active_threads:
            new_thread = TimeTracker(after.server, after).start()
            active_threads.update({after.id:new_thread})
    elif after.voice.voice_channel is None or after.voice.is_afk:
        try:
            del active_threads[after.id]
        except KeyError as e:
            return

#doesn't matter if roles are created, only if deleted.
"""
@bot_event
async def on_server_role_delete(role):
    pass
"""
    

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

#add bot to whitelist as well
#difference between lists for whitelist
#tell people if they're missing permissions
#create check for number of arguments, and time must be greater than ?
#create checks for type of arguments.
#account for server admins manually asigning roles
#allow admins to turn message feature off
#TODO reformat time, ddd:hh:ss
@bot.command(name='ranktime', pass_context=True)
async def rank_time(context, rank, time):
    server_id = context.message.server.id
    times = global_member_times[context.message.server.id]
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
        return
    if not is_pos_number(time):
        await bot.say('The time field must be a number and positive')
        return
    for a_rank in role_orders[server_id]:
        if float(server_configs[server_id][a_rank]) == float(time):
            await bot.say('Sorry, we do not support ranks having the same times '
                            + 'at this moment.')
            return
    new_time = convert_time(time)
    if rank in role_orders[server_id]:
        # Handles several edge cases for when a rank already has a specified
        # time. An event object is set to prevent data corruption between
        # threads while updating ranks. (Note that rank position denotes
        # where each rank is in the time sorted list of roles.)
        #
        # The initial block of function calls before the for loop loop settup 
        # for the updates in ranks. We initialize deep copies of the old 
        # configuration before the updates to be able to compare previous 
        # rank times and rank positions in the hierarchy against the new ones.
        # 
        # For each person in the server, we set references to that person's
        # current rank and previous ranks and the times it takes to get to
        # them if such things exist. We can see that if a current rank does
        # not exists, the current rank time is set to -1. This is to account
        # for when the new time is set to 0. 
        #
        # The first case accounts for when the updated rank is higher than
        # the users current rank but the user has already reached the new time
        # milestone. We update the users rank to the new rank accordingly.
        #
        # The second case accounts for when the user is already at the rank
        # being updated. This contains sub-cases. First, if the user's 
        # accululated voice channel time is now less then the time needed
        # to achieve the rank, we demote the user. Second, if the rank
        # moves down the hierarchy by at lease 2 tiers, we give the user
        # their previous rank.
        #
        # The third case accounts for all other scenarios. That being if
        # the rank being updated moved above or below the user's in the
        # hierarchy without altering the user's rank. In this case, all
        # that must be done is an update to the user's rank position.
        #
        # Finally, we update the next_rank and rank_time fields in then user's
        # appropriate thread if such a thread exists. We then set the event
        # objects flag to true to signal all TimeTracker threads to continue.
        #
        # (I am aware that if one server enters this if statement, threads for
        # users in every server will be blocked. However, in a data sensitive
        # environment such as this, I would argue that prioritizing 
        # the safety of each server's statistics is of greater importance
        # than effeciency.)
        stat_event.clear()
        old_server_configs = copy.deepcopy(server_configs[server_id])
        change_config(server_id, rank, str(time))
        previous_role_orders = copy.deepcopy(role_orders[server_id])
        role_orders.update({server_id:get_roles_in_order(context.message.server)})
        rank_after_new = role_orders[server_id].index(rank)
        rank_before_new = previous_role_orders.index(rank) 
        rank_obj = utils.find(lambda role: role.name == rank, context.message.server.roles)
        for person in times.keys():
            person_obj = utils.find(lambda member: member.id == person,
                                    context.message.server.members)
            if times[person][1] - 1 >= 0:
                curr_rank = previous_role_orders[times[person][1] - 1]
                curr_rank_time = convert_time(old_server_configs[curr_rank])
                curr_rank_pos = role_orders[server_id].index(curr_rank)
            else:
                curr_rank = None
                curr_rank_time = -1
            if times[person][1] - 2 >= 0:
                previous_rank = previous_role_orders[times[person][1] - 2]
                previous_rank_time = convert_time(old_server_configs[previous_rank])
            else:
                previous_rank = None
                previous_rank_time = 0
            if  (curr_rank_time < new_time and times[person][0] >= new_time 
                    and curr_rank != rank):
                try:
                    await bot.replace_roles(person_obj, rank_obj)
                except discord.errors.Forbidden as e:
                    logger.info(person_obj.name + ':' + person + 'Failed to update')
                if times[person][1] < len(role_orders[server_id]):
                    times[person][1] += 1 #sets up next rank
            elif curr_rank is not None and curr_rank == rank:
                if times[person][0] < new_time:
                    if times[person][1] - 1 == 0:
                        try:
                            await bot.remove_roles(person_obj, rank_obj)
                        except discord.errors.Forbidden as e:
                            logger.info(person_obj.name + ':' + person + 'Failed to update')
                            continue
                        curr_rank_pos = role_orders[server_id].index(curr_rank) - 1
                        times[person][1] = 0
                    elif times[person][1] - 1 > 0:
                        previous_role = utils.find(lambda role: previous_rank 
                                                    == role.name, context.message.server.roles)
                        try:
                            await bot.replace_roles(person_obj, previous_role)
                        except discord.errors.Forbidden as e:
                            logger.info(person_obj.name + ':' + person + 'Failed to update')
                            continue
                        times[person][1] -= 1
                elif times[person][0] > new_time and previous_rank_time > new_time:
                    previous_role = utils.find(lambda role: previous_rank
                                                == role.name, context.message.server.roles)
                    try:
                        await bot.replace_roles(person_obj, previous_role)
                    except discord.errors.Forbidden as e:
                        logger.info(person_obj.name + ':' + person + 'Failed to update')
                        continue
            else:
                if (times[person][1] <= len(role_orders[server_id]) and 
                        times[person][1] > 0 and curr_rank is not None and 
                        rank_before_new < curr_rank_pos and
                        rank_after_new > curr_rank_pos):
                    times[person][1] -= 1
                elif (times[person][1] < len(role_orders[server_id]) and
                        times[person][1] >= 0 and curr_rank is not None and
                        rank_before_new > curr_rank_pos and rank_after_new < curr_rank_pos):
                    times[person][1] += 1
            try:
                try:
                    new_rank = role_orders[server_id][times[person][1]]
                    new_rank_time = convert_time(server_configs[server_id][new_rank])
                    active_threads[person].next_rank = new_rank
                    active_threads[person].rank_time = new_rank_time
                except IndexError as e:
                    active_threads[person].rank_time = None
            except (AttributeError, KeyError) as exc:
                continue
        stat_event.set()
    else:
        # This code accounts for new ranks added to the list. It is fairly
        # similar to the code directly above, just with a lot stripped from it.
        old_server_configs = copy.deepcopy(server_configs[server_id])
        change_config(server_id, rank, str(time))
        previous_role_orders = copy.deepcopy(role_orders[server_id])
        role_orders.update({server_id:get_roles_in_order(context.message.server)})
        rank_after_new = role_orders[server_id].index(rank)
        rank_obj = utils.find(lambda role: role.name == rank, context.message.server.roles)
        stat_event.clear()
        for person in times.keys():  
            person_obj = utils.find(lambda member: member.id == person,
                                    context.message.server.members)
            if times[person][1] - 1 >= 0:
                curr_rank = previous_role_orders[times[person][1] - 1]
                curr_rank_time = convert_time(old_server_configs[curr_rank])
                curr_rank_pos = role_orders[server_id].index(curr_rank)
            else:
                curr_rank = None
                curr_rank_time = -1
            if curr_rank_time < new_time and times[person][0] >= new_time:
                try:
                    await bot.replace_roles(person_obj, rank_obj)
                except discord.errors.Forbidden as e:
                    logger.info(person_obj.name + ':' + person + 'Failed to update')
                if times[person][1] < len(role_orders[server_id]):
                    times[person][1] += 1 #sets up next rank
            elif curr_rank is not None and rank_after_new < curr_rank_pos:
                times[person][1] += 1
            try:
                try:
                    new_rank = role_orders[server_id][times[person][1]]
                    new_rank_time = convert_time(server_configs[server_id][new_rank])
                    active_threads[person].next_rank = new_rank
                    active_threads[person].rank_time = new_rank_time
                except IndexError as e:
                    active_threads[person].rank_time = None
            except (AttributeError, KeyError) as exc:
                continue
        stat_event.set()


#------------CHECKS------------#

#------------HELPER METHODS------------#

def stats_start(server):
    if not os.path.isfile(server.id + 'stats.txt'):
        curr_stats = open(server.id + 'stats.txt', 'w')
        global_member_times.update({server.id:dict()})
        for member in server.members:
            global_member_times[server.id].update({member.id:[0, 0]})
            #first element in list = time, second element = rank position
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
            time_and_rank[0] = int(float(time_and_rank[0]))
            time_and_rank[1] = int(time_and_rank[1])
            member_times.update({member_id:time_and_rank})
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
    value = float(time)
    return int(value * 60 * 60)

"""returns a list of rank names sorted by their time"""
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

def update_stats(server):
    with open(server.id + 'stats.txt', 'w') as stats:
        server_times = global_member_times[server.id]
        iterator = iter(server_times)
        first_key = next(iterator)
        stats.write(first_key + '=' + str(server_times[first_key]))
        for key in iterator:
            stats.write(';' + key + '=' + str(server_times[key]))


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
        while (self.member.voice.voice_channel is not None 
                and not self.member.voice.is_afk):
            times[self.member.id][0] = self.member_time + time.time() - now
            stat_event.wait()
            if (self.rank_time is not None and 
                    self.member_time + time.time() >= self.rank_time + now):
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
                update_stats(self.server)
            time.sleep(config['wait_time'])


class PeriodicUpdater(threading.Thread):

    def __init__(self):
        super().__init__()

    def run(self):
        while threading.main_thread().is_alive():
            for server in bot.servers:
                try:
                    update_stats(server)
                except KeyError as e:
                    logger.info('KeyError in PeriodicUpdater thread.')
            time.sleep(config['sleep_time'])

bot.run(config['token'])
