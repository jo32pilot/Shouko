import re
import sys
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
from discord.ext import commands
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
server_wl = dict()
server_events = dict()
active_threads = dict()
#active_threads prevents creation of threads due to muting and deafening events
bot.remove_command('help')
with open('config.json', 'r') as file:
    config = json.load(file)

#------------EVENTS------------#

@bot.event
async def on_ready():
    for server in bot.servers:
        await bot.on_server_join(server)
    PeriodicUpdater().start()
    await bot.change_presence(game=Game(name='on a ferris wheel.'))
    logger.info(str(server_configs))

@bot.event
async def on_server_join(server):
    stats_start(server)
    config_start(server)
    role_orders.update({server.id:get_roles_in_order(server)})
    whitelist_start(server)
    active_threads.update({server.id:dict()})
    stat_event = threading.Event()
    stat_event.set()
    server_events.update({server.id:stat_event})

@bot.event
async def on_server_remove(server):
    try:
        del global_member_times[server.id]
        del server_configs[server.id]
        del role_orders[server.id]
        del server_wl[server.id]
        del server_events[server.id]
    except (ValueError, KeyError) as e:
        logger.error('Failed to remove server information from ' + server.id)
    try:
        for person in active_threads[server.id]:
            active_threads[server.id][person].bot_in_server = False
        del active_threads[server.id]
    except KeyError as e:
        return

@bot.event
async def on_voice_state_update(before, after):
    if after.voice.voice_channel is not None and not after.voice.is_afk:
        if after.id not in active_threads[after.server.id]:
            new_thread = TimeTracker(after.server, after)
            new_thread.start()
            active_threads[after.server.id].update({after.id:new_thread})
    elif after.voice.voice_channel is None or after.voice.is_afk:
        try:
            del active_threads[after.server.id][after.id]
        except KeyError as e:
            return

@bot.event
async def on_server_role_create(role):
    reciever = role.server.default_channel
    if (utils.find(lambda rank: role.name == rank.name and 
            role.id != rank.id, role.server.roles) is not None):
        if reciever is None or reciever.type != ChannelType.text:
            reciever = role.server.owner
        await bot.send_message(reciever, content=(
                'Hey, I that see you, or someone else with permission, '
                + 'created a role with the same name as '
                + 'another role on your server. Just a reminder that, '
                + 'before configuring any rank times, please be aware '
                + 'that I cannot guarantee that the correct version '
                + 'will be assigned and that any reconfiuration of '
                + 'said rank may cause the ranking system to fail. '
                + 'If such issues do surface, please follow these steps:\n\n'
                + '1: Remove the duplicate role from the server.\n2: Use the '
                + 'ranktime command to re-add the desired rank and time.\n3: '
                + 'Use the ~cleanslate command to reset member ranks.\n\n'
                + 'Be assured that all accumulated voice channel times for '
                + 'each member will be preserved and rejoining a voice channel '
                + 'will return everyone\'s deserved ranks.'))
        if reciever is not role.server.default_channel:
            await bot.send_message(reciever, content=(
                    + '\nIf you don\'t want to recieve '
                    + 'any of these messages directly, settup a default channel '
                    + 'in you server and I\'ll send these over there!'))

@bot.event
async def on_server_role_delete(role):
    server_id = role.server.id
    if role.name not in server_configs[server_id]:
        return
    times = global_member_times[server_id]
    server_events[server_id].clear()
    for person in active_threads[server_id]:
        while not active_threads[server_id][person].block_update:
            continue
    role_index = role_orders[server_id].index(role.name)
    old_server_configs = copy.deepcopy(server_configs[server_id])
    previous_role_orders = copy.deepcopy(role_orders[server_id])
    delete_config(server_id, role.name)
    role_orders[server_id].remove(role.name)
    for person in (set(times.keys()) - set(server_wl[server_id])):
        person_obj = utils.find(lambda member: member.id == person,
                                role.server.members)
        given_roles = [role for role in person_obj.roles if role.name not in role_orders[server_id]]
        if times[person][1] - 1 >= 0:
            curr_rank = previous_role_orders[times[person][1] - 1]
            curr_rank_time = convert_time(old_server_configs[curr_rank])
            curr_rank_pos = previous_role_orders.index(curr_rank)
        else:
            curr_rank = None
            curr_rank_time = -1
            curr_rank_pos = -1
        if times[person][1] - 2 >= 0:
            previous_rank = previous_role_orders[times[person][1] - 2]
        else:
            previous_rank = None
        if curr_rank is not None and curr_rank == role.name:
            try:
                await bot.replace_roles(person_obj, utils.find(lambda prev_obj: (
                                    prev_obj.name == previous_rank), role.server.roles), *given_roles)
            except (discord.errors.Forbidden, AttributeError) as e:
                logger.info(person_obj.name + ':' + person + ' : Exception Occured')
            times[person][1] -= 1
        else:
            if (times[person][1] > 0 and curr_rank is not None and
                    role_index < curr_rank_pos):
                times[person][1] -= 1
        try:
            try:
                next_rank = role_orders[server_id][times[person][1]]
                next_rank_time = convert_time(server_configs[server_id][next_rank])
                active_threads[server_id][person].next_rank = next_rank
                active_threads[server_id][person].rank_time = next_rank_time
            except IndexError as e:
                active_threads[server_id][person].rank_time = None
        except (AttributeError, KeyError) as exc:
            continue
    server_events[server_id].set()

# cannot implement on_member_update event to account for manually assigned 
# ranks because:
# 1: Would recursively call itself I tried to fix ranks.
# 2: Would get called even if a member ranked up normally.



#------------COMMANDS------------#



@bot.command(pass_context=True)
async def help(context, *cmd):
    embeder = Embed(colour=26575, type='rich')
    try:
        command = cmd[0]
        embeder.add_field(name=config[command][0], value=config[command][1])
    except (ValueError, KeyError, IndexError) as e:
        embeder.title = 'Command List'
        embeder.description = ('~help settup\n~help my_time\n~help leaderboard'
                        + '\n~help whitelist\n'
                        + '~help unwhitelist\n~help whitelist_all\n~'
                        + 'help unwhitelist_all\n~help list_whitelist\n'
                        + '~help cleanslate\n~help ranktime\n'
                        + '~help rm_ranktime\n~help toggle_messages'
                        + '\n~github\n~donate')
    await bot.send_message(context.message.channel, embed=embeder)

@bot.command(pass_context=True)
async def settup(context):
    server_id = context.message.server.id
    to_send = ''
    for role in role_orders[server_id][::-1]:
        to_send = (to_send + role + ': ' 
                + server_configs[server_id][role] + '\n')
    embeder = Embed(title='Rank Settup', colour=3866383, type='rich', description=to_send)
    await bot.send_message(context.message.channel, embed=embeder)
    logger.debug(embeder.fields)

@bot.command(pass_context=True)
async def my_time(context):
    times = global_member_times[context.message.server.id]
    time = convert_from_seconds(times[context.message.author.id][0])
    await bot.say('%s Hours, %s Minutes, %s Seconds' % time)

@bot.command(pass_context=True)
async def leaderboard(context):
    server = context.message.server
    times = global_member_times[server.id]
    embeder = Embed(title='Top 5 Server Member Times', colour=16755456, type='rich')
    to_sort = dict()
    for person in times:
        to_sort.update({person:times[person][0]})
    sorted_list = sorted(to_sort, key=to_sort.get)
    thumbnail = None
    top_five = -6
    for person in sorted_list[:top_five:-1]:
        try:
            top_memb = utils.find(lambda member: member.id == person, server.members)
            time_spent = convert_from_seconds(times[person][0])
            if thumbnail is None:
                thumbnail = top_memb.avatar_url
            if thumbnail == '':
                thumbnail = top_memb.dafault_avatar_url
            embeder.add_field(name=top_memb.name, value=
                    ('%s Hours, %s minutes, and %s seconds' % time_spent))
        except (AttributeError, ValueError, KeyError) as e:
            top_five -= 1
    embeder.set_thumbnail(url=thumbnail)
    await bot.send_message(context.message.channel, embed=embeder)
        

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
async def whitelist(context, *name):
    server = context.message.server
    to_list = find_user(server, name)
    if to_list is None:
        await bot.say('Sorry! I can\'t find this person. '
                + 'Remember that the format for this command is \n\n'
                + '`~whitelist [discord_username#XXXX]` (Names are case sensitive)'
                + '\n\nExample usage: ```~whitelist Shouko Nishimiya#1234```')
    elif to_list.id in server_wl[server.id]:
        await bot.say('Member is already on the whitelist.')
    else:
        server_wl[server.id].append(to_list.id)
        write_wl(server, to_list.id)
        await bot.say('Whitelist successful!')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
async def unwhitelist(context, *name):
    server = context.message.server
    times = global_member_times[server.id]
    to_list = find_user(server, name)
    if to_list is None:
        await bot.say('Sorry! I can\'t find this person. '
                + 'Remember that the format for this command is\n\n'
                + '`~unwhitelist [discord_username#XXXX]` (Names are case sensitive)'
                + '\n\n Example usage: ```~unwhitelist Shouko Nishimiya#1234```')
    elif to_list.id not in server_wl[server.id]:
        await bot.say('Member is already not on the whitelist.')
    elif to_list.id in server_wl[server.id]:
        server_wl[server.id].remove(to_list.id)
        times[to_list.id][1] = 0
        try:
            try:
                new_rank = role_orders[server.id][times[to_list.id][1]]
                new_rank_time = convert_time(server_configs[server.id][new_rank])
                active_threads[server.id][to_list.id].next_rank = new_rank
                active_threads[server.id][to_list.id].rank_time = new_rank_time
            except IndexError as e:
                active_threads[server.id][to_list.id].rank_time = None
        except (AttributeError, KeyError) as exc:
            pass
        with open(server.id + 'wl.txt', 'w+') as wl_file:
            try:
                wl_file.write(server_wl[server.id][0])
                for person in server_wl[server.id][1:]:
                    wl_file.write(';' + person)
            except IndexError as e:
                return
        await bot.say('Member has been removed from the whitelist! Rank '
                        + 'should be given back after rejoining a voice '
                        + 'channel if not already returned.')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
async def whitelist_all(context):
    server = context.message.server
    for person in server:
        if person not in server_wl[server.id]:
            server_wl[server.id].append(person.id)
    with open(server.id + 'wl.txt', 'w+') as wl_file:
        try:
            wl_file.write(server[server.id][0])
            for person in server_wl[server.id][1:]:
                wl_write.write(';' + person)
        except IndexError as e:
            await bot.say('Done!')
            return
    await bot.say('Done!')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
async def unwhitelist_all(context):
    server = context.message.server
    times = global_member_times[server.id]
    server_wl[server.id].clear()
    for person in times:
        times[person][1] = 0
        try:
            try: 
                new_rank = role_orders[server.id][times[person][1]]
                new_rank_time = convert_time(server_configs[server.id][new_rank])
                active_threads[server.id][person].next_rank = new_rank
                active_threads[server.id][person].rank_time = new_rank_time
            except IndexError as e:
                active_threads[server.id][person].rank_time = None
        except (AttributeError, KeyError) as exc:
            pass
    open(server.id + 'wl.txt' , 'w').close()
    await bot.say('Done!')

@bot.command(pass_context=True)
async def list_whitelist(context):
    server = context.message.server
    to_send = ''
    for person in server_wl[server.id]:
        to_list = utils.find(lambda member: member.id == person, server.members)
        to_send = to_send + to_list.name + '#' + to_list.discriminator + '\n'
    embeder = Embed(title='Whitelist', colour=16777215, type='rich', description=to_send)
    await bot.send_message(context.message.channel, embed=embeder)

@bot.command(name='cleanslate', pass_context=True)
@commands.has_permissions(manage_roles=True)
async def clean_slate(context):
    times = global_member_times[context.message.server.id]
    for person in times:
        times[person][1] = 0
    await bot.say('Done!')

@bot.command(name='ranktime', pass_context=True)
@commands.has_permissions(manage_roles=True)
async def rank_time(context, *args):
    rank = ' '.join(args[:-1])
    time = args[-1]
    server_id = context.message.server.id
    times = global_member_times[server_id]
    count = 0
    for role in context.message.server.roles:
        if role.name == rank:
            count += 1
            if(count > 1):
                await bot.say('Cannot change rank time if multiple ranks have the '
                                + 'same name.')
                return
    if count == 0:
        await bot.say('Cannot find a role with the name %s.' % rank)
        return
    for a_rank in role_orders[server_id]:
        if (server_configs[server_id][a_rank]) == time:
            await bot.say('Sorry, we do not support ranks having the same times '
                            + 'at this moment.')
            return
    new_time = convert_time(time)
    if new_time == None:
        await bot.say('The formatting of your time argument is incorrect.\n' 
                        + 'Usage: `~ranktime [role_name] [hhh:mm:ss]`\n'
                        + 'Example: ```~ranktime A Cool Role 002:06:34```')
    if rank in role_orders[server_id]:
        # Handles several edge cases for when a rank already has a specified
        # time. An event object is set to prevent data corruption between
        # threads while updating ranks. (Note that rank position denotes
        # where each rank is in the time sorted list of roles.)
        #
        # The initial block of function calls before the for loop settup 
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
        server_events[server_id].clear()
        for person in active_threads[server_id]:
            while not active_threads[server_id][person].block_update:
                continue
        old_server_configs = copy.deepcopy(server_configs[server_id])
        change_config(server_id, rank, str(time))
        previous_role_orders = copy.deepcopy(role_orders[server_id])
        role_orders.update({server_id:get_roles_in_order(context.message.server)})
        rank_after_new = role_orders[server_id].index(rank)
        rank_before_new = previous_role_orders.index(rank) 
        rank_obj = utils.find(lambda role: role.name == rank, context.message.server.roles)
        for person in (set(times.keys()) - set(server_wl[server_id])): # <-- super ineffecient
            person_obj = utils.find(lambda member: member.id == person,
                                    context.message.server.members)
            try:
                given_roles = [role for role in person_obj.roles if role.name not in role_orders[server_id]]
            except AttributeError as e:
                pass
            if times[person][1] - 1 >= 0:
                curr_rank = previous_role_orders[times[person][1] - 1]
                curr_rank_time = convert_time(old_server_configs[curr_rank])
                curr_rank_pos = previous_role_orders.index(curr_rank) 
            else:
                curr_rank = None
                curr_rank_time = -1
                curr_rank_pos = -1
            if times[person][1] - 2 >= 0:
                previous_rank = previous_role_orders[times[person][1] - 2]
                previous_rank_time = convert_time(old_server_configs[previous_rank])
            else:
                previous_rank = None
                previous_rank_time = 0
            if  (curr_rank_time < new_time and times[person][0] >= new_time 
                    and curr_rank != rank):
                try:
                    await bot.replace_roles(person_obj, rank_obj, *given_roles)
                except discord.errors.Forbidden as e:
                    logger.info(person_obj.name + ':' + person + 'Failed to update')
                if (times[person][1] < len(role_orders[server_id]) and 
                        rank_before_new > curr_rank_pos):
                    times[person][1] += 1 #sets up next rank
            elif curr_rank is not None and curr_rank == rank:
                if times[person][0] < new_time:
                    if times[person][1] - 1 == 0:
                        try:
                            await bot.replace_roles(person_obj, *given_roles)
                        except discord.errors.Forbidden as e:
                            logger.info(person_obj.name + ':' + person + 'Failed to update')
                            continue
                    elif times[person][1] - 1 > 0:
                        previous_role = utils.find(lambda role: previous_rank 
                                                    == role.name, context.message.server.roles)
                        try:
                            await bot.replace_roles(person_obj, previous_role, *given_roles)
                        except discord.errors.Forbidden as e:
                            logger.info(person_obj.name + ':' + person + 'Failed to update')
                            continue
                    times[person][1] -= 1
                elif times[person][0] > new_time and previous_rank_time > new_time:
                    previous_role = utils.find(lambda role: previous_rank
                                                == role.name, context.message.server.roles)
                    try:
                        await bot.replace_roles(person_obj, previous_role, *given_roles)
                    except discord.errors.Forbidden as e:
                        logger.info(person_obj.name + ':' + person + 'Failed to update')
                        continue
            else:
                if (times[person][1] > 0 and curr_rank is not None and 
                        rank_before_new < curr_rank_pos and
                        rank_after_new >= curr_rank_pos):
                    times[person][1] -= 1
                elif (times[person][1] < len(role_orders[server_id]) and
                        curr_rank is not None and
                        rank_before_new > curr_rank_pos and 
                        rank_after_new <= curr_rank_pos):
                    times[person][1] += 1
            try:
                try:
                    new_rank = role_orders[server_id][times[person][1]]
                    new_rank_time = convert_time(server_configs[server_id][new_rank])
                    active_threads[server_id][person].next_rank = new_rank
                    active_threads[server_id][person].rank_time = new_rank_time
                except IndexError as e:
                    active_threads[server_id][person].rank_time = None
            except (AttributeError, KeyError) as exc:
                continue
        server_events[server_id].set()
    else:
        # This code accounts for new ranks added to the list. It is fairly
        # similar to the code directly above with a lot stripped from it.
        server_events[server_id].clear()
        for person in active_threads[server_id]:
            while not active_threads[server_id][person].block_update:
                continue
        old_server_configs = copy.deepcopy(server_configs[server_id])
        change_config(server_id, rank, str(time))
        previous_role_orders = copy.deepcopy(role_orders[server_id])
        role_orders.update({server_id:get_roles_in_order(context.message.server)})
        rank_after_new = role_orders[server_id].index(rank)
        rank_obj = utils.find(lambda role: role.name == rank, context.message.server.roles)
        for person in (set(times.keys()) - set(server_wl[server_id])):
            person_obj = utils.find(lambda member: member.id == person,
                                    context.message.server.members)
            given_roles = [role for role in person_obj.roles if role.name not in role_orders[server_id]]
            if times[person][1] - 1 >= 0:
                curr_rank = previous_role_orders[times[person][1] - 1]
                curr_rank_time = convert_time(old_server_configs[curr_rank])
                curr_rank_pos = role_orders[server_id].index(curr_rank)
            else:
                curr_rank = None
                curr_rank_time = -1
            if curr_rank_time < new_time and times[person][0] >= new_time:
                try:
                    await bot.replace_roles(person_obj, rank_obj, *given_roles)
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
                    active_threads[server_id][person].next_rank = new_rank
                    active_threads[server_id][person].rank_time = new_rank_time
                except IndexError as e:
                    active_threads[server_id][person].rank_time = None
            except (AttributeError, KeyError) as exc:
                continue
        server_events[server_id].set()
    await bot.say('Done!')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
async def rm_ranktime(context, *args):
    server = context.message.server
    rank = ' '.join(args)
    if rank not in role_orders[server.id]:
        bot.say('Cannot find rank with the name %s.'
                + 'Usage: `~ranktime [role_name] [hhh:mm:ss]`\n'
                + 'Example: ```~ranktime A Cool Role 002:06:34```' % rank)
    else:
        await on_role_delete(lambda role: role.name == rank, context.message.server.roles)
        await bot.say('Done!')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
async def toggle_messages(context):
    server_id = context.message.server.id
    change_config(server_id, "_send_messages324906", 
            not bool(server_configs[server_id]["_send_message324906"]))

@bot.command(pass_context=True)
async def github(context):
    embeder = Embed(colour=26575, type='rich', description=config['github_url'])
    await bot.send_message(context.message.channel, embed=embeder)

@bot.command(pass_context=True)
async def donate(context):
    embeder = Embed(colour=26575, type='rich', description=config['patreon'])
    await bot.send_message(context.message.channel, embed=embeder)



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

# the name _send_messages324906 is of no significance. just afraid that,
# because this option and the ranks are stored in the same dictionary
# and file, someone might have a role named "send message" so I wanted
# to make the option name unique. Didn't want to make another file and
# dictionary for open option
def config_start(server):
    if not os.path.isfile(server.id + '.txt'):
        curr_config = open(server.id + '.txt', 'w+')
        settings = {"_send_messages324906":True}
        curr_config.write("_send_messages324906" + "=" + "True")
    else:
        curr_config = open(server.id + '.txt', 'r')
        readable = curr_config.read()
        settings = dict(pair.split('=') for pair in readable.split(';'))
    server_configs.update({server.id:settings})
    curr_config.close()

def whitelist_start(server):
    if not os.path.isfile(server.id + 'wl.txt'):
        curr_wl = open(server.id + 'wl.txt', 'w+')
        people = list()
    elif os.stat(server.id + 'wl.txt').st_size == 0:
        server_wl.update({server.id:list()})
        return
    else:
        curr_wl = open(server.id + 'wl.txt', 'r')
        people = curr_wl.read().split(';')
    server_wl.update({server.id:people})
    curr_wl.close()

def write_wl(server, person_id):
    with open(server.id + 'wl.txt', 'a') as curr_wl:
        if os.stat(server.id + 'wl.txt').st_size == 0:
            curr_wl.write(person_id)
        else:
            curr_wl.write(';' + person_id)

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

def delete_config(server_id, option):
    settings = server_configs[server_id]
    del settings[option]
    new_config = open(server_id + '.txt', 'w+')
    iterator = iter(settings)
    first_key = next(iterator)
    new_config.write(first_key + '=' + str(settings[first_key]))
    for key in iterator:
        new_config.write(';' + key + '=' + str(settings[key]))
    new_config.close()

def convert_time(time):
    try:
        hours, minutes, seconds = time.split(':')
        hours = int(hours) * 60 * 60
        minutes = int(minutes) * 60
        seconds = int(seconds)
        final_time = hours + minutes + seconds
        if final_time < 0:
            return None
        return final_time
    except ValueError as e:
        return None

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


def update_stats(server):
    with open(server.id + 'stats.txt', 'w') as stats:
        server_times = global_member_times[server.id]
        iterator = iter(server_times)
        first_key = next(iterator)
        stats.write(first_key + '=' + str(server_times[first_key]))
        for key in iterator:
            stats.write(';' + key + '=' + str(server_times[key]))

def find_user(server, name_list):
    discrim_pattern = r"[0-9]{4}"
    try:
        last_name, discrim = name_list[-1].split('#')
        if re.match(discrim_pattern, discrim):
            username = ' '
            username = username.join(name_list[:-1])
            if username == '':
                username = last_name
            else:
                username = username + ' ' + last_name
            to_list = utils.find((lambda person: person.name == username and
                                    person.discriminator == discrim), server.members)
            if to_list is None:
                return None
            else:
                return to_list
        else:
            return None
    except ValueError as e:
        return None

def convert_from_seconds(time):
    time = int(time)
    seconds = (time % 60)
    minutes = int(((time - seconds) / 60) % 60)
    hours = int((time - (minutes * 60) - seconds) / 60 / 60)
    return (str(hours), str(minutes), str(seconds))



#------------THREADING CLASSES------------#



class TimeTracker(threading.Thread):

    def __init__(self, server, member):
        super().__init__(daemon=True)
        times = global_member_times[server.id]
        self.server = server
        self.member = member
        self.member_time = times[member.id][0]
        self.bot_in_server = True
        self.stat_event = server_events[server.id]
        self.block_update = False
        try:
            self.next_rank = role_orders[server.id][times[member.id][1]]
            self.rank_time = convert_time(server_configs[server.id][self.next_rank])
        except IndexError as e:
            self.rank_time = None

    def run(self):
        now = time.time()
        times = global_member_times[self.server.id]
        while (self.member.voice.voice_channel is not None 
                and not self.member.voice.is_afk and self.bot_in_server):
            times[self.member.id][0] = self.member_time + time.time() - now
            self.block_update = True
            self.stat_event.wait()
            self.block_update = False
            if (self.member.id not in server_wl[self.server.id] and 
                    self.rank_time is not None and 
                    self.member_time + time.time() >= self.rank_time + now):
                given_roles = [role for role in self.member.roles if role.name not in role_orders[self.server.id]]
                future = asyncio.run_coroutine_threadsafe(
                        bot.replace_roles(self.member, utils.find(
                        lambda role: role.name == self.next_rank
                        , self.server.roles), *given_roles), bot.loop)
                future.result()
                times[self.member.id][1] += 1
                hours, minutes, seconds = convert_from_seconds(times[self.member.id][0])
                reciever = self.server.default_channel
                fmt_tup = (self.member.mention, hours, minutes, seconds,
                                self.next_rank)
                message = ("Congratulations %s! You've spent a total of "
                            + "in %s hours, %s minutes, and %s seconds in this "
                            + "servers' voice channels and have therefore "
                            + "earned the rank of %s! ") % fmt_tup
                if (reciever is not None and reciever.type == ChannelType.text and
                        bool(server_configs[self.server.id]["_send_messages324906"])):
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
