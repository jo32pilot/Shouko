"""

Defines functionality for the discord bot, Shouko. Keeps track of user time
spent in voice channels. Server members with role managing permissions can
allow the bot to assign discord roles based on accumulated time.

Example:

    $ python3 discordTimeRanker.py


Attributes:
    bot (Bot): The bot object running on each server.

    server_configs (dict): Holds (server_id, dict) pairs where 
        server_id indicates what server the dictionary value belongs to. The 
        dictionary value holds mainly (role, time_milestone) pairs where
        role is assigned to any member in the same server upon reaching the
        time_milestone (in seconds). Also is meant to hold other server 
        configurations, the only other one being (_send_messages324906, bool) 
        where if bool is true, upon rank up, congratulations are sent to the 
        server's defaultchannel, otherwise it is sent directly to the user.
        Note that:
            server_id is always (string),
            time_milestone is always (int).

    global_member_times (dict): Holds (server_id, dict) pairs where server_id
        indicates what server the dictionary value belongs to. The dictionary
        value holds (user_id, list) pairs where user_id is a unique user id
        assigned by Discord and list holds two elemnts, [time, role]. Time
        is an integer representing a users total accumulated time.
        Role is an integer representing the users current role in the role
        hierarchy organized by role orders.
        Ex.

                    Role Hierarchy: [Peasant, Craftsman, Noble, Royalty]
            Integer Representation: [      0,         1,     2,       3]

            [1000, 2] means user_id is a Noble with 1000 seconds spent in
            the server's voice channels.

        Note that:
            user_id is always (string)

    role_orders (dict): Holds (server_id, list) pairs where server_id indicates
        what server the list value belongs to. The list holds server roles
        ordered by their time_milestones in ascending order. This way, we
        now where each role stands in the heirarchy and can represent each
        person's role as an integer

    server_wl (dict): Holds (server_id, list) pairs where server_id indicates
        what server the list value belongs to. The list holds whitelisted 
        user_ids for their server. A whitelisted user will not be affected
        by automatic roll assigning but will still accumulate time for staying
        in voice channels. Whitelisted users can be manually assigned roles
        without reprecussions. (As in the bot's functionality might break for
        that specific server).

    server_events (dict): Holds (server_id, Event) pairs where server_id 
        indicates what server the Event value belongs to. Event is the Event 
        object from the threading module. Used to help prevent race conditions.

    active_threads (dict): Holds (server_id, dict) pairs where server_id
        indicates what server the dict value belongs to. The dictionary
        value holds (user_id, TimeTracker) pairs. TimeTracker extends the 
        Thread from the threading module. TimeTracker is explained in the class
        definition.

    config (dict): Holds (key, value) pairs parsed from config.json.


"""
import re
import sys
import json
import time
import copy
import asyncio
import logging
import os.path
import discord
import traceback
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
                            , encoding='utf-8', mode='w')
handler.setFormatter(
        logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

#------------SETTUP / ATTRIBUTES------------#

bot = Bot(command_prefix='~', case_insensitve=True)
server_configs = dict()
global_member_times = dict()
role_orders = dict()
server_wl = dict()
server_events = dict()
active_threads = dict()
bot.remove_command('help')
with open('config.json', 'r') as file:
    config = json.load(file)

#------------EVENTS------------#
# NOTE: All event function headers are explained in the reference for the 
# API.

@bot.event
async def on_ready():
    """Event called when bot begins to run.

    Calls on_server_join event for each server to set up server stats and
    configurations. Also begins the PeriodicUpdater thread. PeriodicUpdater
    is explained in the class definition.

    """
    for server in bot.servers:
        await bot.on_server_join(server)

    PeriodicUpdater().start()
    await bot.change_presence(game=Game(name='on a ferris wheel | ~help'))
    logger.info(str(server_configs))


@bot.event
async def on_server_join(server):
    """Event called when bot joins the server.

    Creates appropriate files to write server configurations and stats to.
    Sets up appropriate attribute dictionaries for the server joined. Also
    starts TimeTracker threads to start accumulating times for users 
    in voice channels upon the bot joining.

    """

    # On servers with 2500 or more members (not a definitive lower bound, could 
    # be more or less) the files begin to get written with hexdecimal values in
    # odd places. This renders my method of parsing text files useless so I
    # have to blacklist those servers. Plan to move config and stats to a 
    # database to alleviate this problem.
    if server.id in config["server_blacklist"]:
        return

    if server.name is not None:
        logger.info('Joining server ' + server.name)

    # Fills up attribute dictionaries and creates appropriate text files.
    stats_start(server)
    config_start(server)
    role_orders.update({server.id:get_roles_in_order(server)})
    whitelist_start(server)
    active_threads.update({server.id:dict()})
    stat_event = threading.Event()
    stat_event.set()
    server_events.update({server.id:stat_event})

    # Check if people joined since bot was last on since on_ready relies on this
    # function as well.
    for person in server.members:
        check_stats_presence(person) 

    # Start TimeTrackers threads for people in voice channels.
    for channel in server.channels:
        for person in channel.voice_members:
            m_voice = person.voice
            if (not m_voice.is_afk and not m_voice.deaf and not m_voice.self_deaf
                    and person.id not in active_threads[server.id]):
                new_thread = TimeTracker(server, person)
                new_thread.start()
                active_threads[server.id].update({person.id:new_thread})

@bot.event
async def on_server_remove(server):
    """Event called when bot leaves a server.

    Removes from attribute dictionaries all server configs and stats for the
    specified server. Note that 

    """
    logger.info('Leaving server ' + server.name)

    # Don't do anything if leaving a blacklisted server, no values were created
    # in the first place.
    if server.id in config["server_blacklist"]:
        return

    # Deletion of dictionary values.
    try:
        del global_member_times[server.id]
        del server_configs[server.id]
        del role_orders[server.id]
        del server_wl[server.id]
        del server_events[server.id]
    except (ValueError, KeyError) as e:
        logger.error('Failed to remove server information from ' + server.id)

    # Stops all running threads in that server.
    try:
        for person in active_threads[server.id]:
            active_threads[server.id][person].bot_in_server = False
        del active_threads[server.id]
    except KeyError as e:
        return

@bot.event
async def on_voice_state_update(before, after):
    """Event called whenever a user's voice state changes.

    Checks various cases and acts on TimeTracker thread accordingly.
    If the user is deafened, stop accumulating time.
    If the user is in an afk channel, stop accumulating time.
    Otherwise, as long as the user is in a voice channel, accumulate time.
    
    """
    if before.server.id in config["server_blacklist"]:
        return

    # Check if user is not deafened or afk and in a voice channel.
    if (after.voice.voice_channel is not None and not after.voice.is_afk
            and not after.voice.deaf and not after.voice.self_deaf):

        # Possible another event occured that still allows user to have time 
        # kept. Checks if that is the case by checking if there is already a 
        # thread for the user.
        if after.id not in active_threads[after.server.id]:
            new_thread = TimeTracker(after.server, after)
            new_thread.start()
            active_threads[after.server.id].update({after.id:new_thread})

    # Otherwise, check if we should stop accumulating time for the user.
    elif (after.voice.voice_channel is None or after.voice.is_afk
            or after.voice.deaf or after.voice.self_deaf):
        try:
            del active_threads[after.server.id][after.id]
        except KeyError as e:
            return

@bot.event
async def on_member_join(member):
    if member.server.id in config["server_blacklist"]:
        return
    check_stats_presence(member) 

@bot.event
async def on_server_role_create(role):
    """Event called when a new role is added to the server.

    It is possible to have multiple roles with the same name in the server.
    If that happens, there is no guarentee of the bot's proper functionality
    in the server where the role was created.
    This function warns the server where such a role was created and informs
    them in how to activate the failsafe in the event that the bot does break.
        
    """
    if role.server.id in config["server_blacklist"]:
        return

    reciever = role.server.default_channel

    # Checks if at least two ranks have the same name but have a different id.
    if (utils.find(lambda rank: role.name == rank.name and 
            role.id != rank.id, role.server.roles) is not None):

        # Checks where to send the message.
        if reciever is None or reciever.type != ChannelType.text:
            reciever = role.server.owner

        await bot.send_message(reciever, content=(
                'Hey, I that see you, or someone else with permission, '
                + 'created a role with the same name as '
                + 'another role on your server. Just a reminder that, '
                + 'before configuring any rank times, please be aware '
                + 'that I cannot guarantee that the correct version '
                + 'will be assigned and that any reconfiguration of '
                + 'said rank may cause the ranking system to fail. '
                + 'If such issues do surface, please follow these steps:\n\n'
                + '1: Remove the duplicate role from the server.\n2: Use the '
                + 'ranktime command to re-add the desired rank and time.\n3: '
                + 'Use the ~cleanslate command to reset member ranks.\n\n'
                + 'Be assured that all accumulated voice channel times for '
                + 'each member will be preserved and rejoining a voice channel '
                + 'will return everyone\'s deserved ranks.'))
        if reciever is not role.server.default_channel:
            to_send = 'If you don\'t want to recieve any of these messages directly, settup a default channel in your server and I\'ll send these over there!'
            await bot.send_message(reciever, content=to_send)

@bot.event
async def on_server_role_delete(role):
    """Event called when a server deletes a role.

    Updates underlying ranking structure settup in the attribute dictionaries.
    Also reassigns roles accordingly.
        
    """
    server_id = role.server.id
    if server_id in config["server_blacklist"]:
        return

    # If role didn't have a time associated with it, don't do anything.
    if role.name not in server_configs[server_id]:
        return
    times = global_member_times[server_id]

    # Block any threads that might cause a race condition until roles are done
    # updating.
    server_events[server_id].clear()
    for person in active_threads[server_id]:
        while not active_threads[server_id][person].block_update:
            continue

    # Reorder role heirarchy, again, based on their associated times in
    # ascending order.
    role_index = role_orders[server_id].index(role.name)
    old_server_configs = copy.deepcopy(server_configs[server_id])
    previous_role_orders = copy.deepcopy(role_orders[server_id])

    # Remove the role and any configuartions relying on it.
    delete_config(server_id, role.name)
    role_orders[server_id].remove(role.name)

    # Looping over the difference in the two sets to ignore people on the
    # whitelist.
    for person in (set(times.keys()) - set(server_wl[server_id])):
        person_obj = utils.find(lambda member: member.id == person,
                                role.server.members)

        # given_roles is a list of a user's roles that do not have a time
        # milestone associated with them. These should be returned to the user.
        given_roles = [role for role in person_obj.roles if role.name not in role_orders[server_id]]

        # If the user has achieved at least the role with the lowest time
        # milestone, they could be affected
        if times[person][1] - 1 >= 0:
            curr_rank = previous_role_orders[times[person][1] - 1]
            curr_rank_time = convert_time(old_server_configs[curr_rank])
            curr_rank_pos = previous_role_orders.index(curr_rank)
        else:
            curr_rank = None
            curr_rank_time = -1
            curr_rank_pos = -1

        # If the user can demote one role (they have a role below them that
        # they can revert back to)
        if times[person][1] - 2 >= 0:
            previous_rank = previous_role_orders[times[person][1] - 2]
        else:
            previous_rank = None

        # If the user is the role that is being removed.
        if curr_rank is not None and curr_rank == role.name:

            # Attempt to revoke their current role and replace it with a lower
            # role or none at all (and give them back given_roles)
            try:
                await bot.replace_roles(person_obj, utils.find(lambda prev_obj: (
                                    prev_obj.name == previous_rank), role.server.roles), *given_roles)
            except (discord.errors.Forbidden, AttributeError) as e:
                logger.info(person_obj.name + ':' + person + ' : Exception Occured')
            times[person][1] -= 1
        
        # User might have a role with higher time milestone than the one being
        # removed. If so, just update underlying ranking structure to represent
        # this. 
        elif (times[person][1] > 0 and curr_rank is not None and
                role_index < curr_rank_pos):
            times[person][1] -= 1

        # Attempts to update some TimeTracker fields which are needed to update
        # roles properly.
        try:
            
            try:
                next_rank = role_orders[server_id][times[person][1]]
                next_rank_time = convert_time(server_configs[server_id][next_rank])
                active_threads[server_id][person].next_rank = next_rank
                active_threads[server_id][person].rank_time = next_rank_time

            # Handles if user is already highest role.
            except IndexError as e:
                active_threads[server_id][person].rank_time = None

        # Thread might not exist.
        except (AttributeError, KeyError) as exc:
            continue
    server_events[server_id].set()

@bot.event
async def on_command_error(error, context):
    """Event called when an error is raised.

    Handles all known errors and logs all others.

    """
    channel = context.message.channel

    if isinstance(error, commands.CommandNotFound):
        pass

    elif isinstance(error, commands.MissingRequiredArgument):
        await bot.send_message(channel, "You didn't provide me enough arguments."
                            + " Checkout out the ~help command and try again!")

    elif isinstance(error, commands.CheckFailure):
        if context.message.server.id in config['server_blacklist']:
            await bot.send_message(channel, "Commands cannot be done in this server due to complications.")
        else:
            await bot.send_message(channel, "You're missing role managing permissions!")

    else:
        embeder = Embed(type='rich', description="Sorry! I ran into an error. "
                        + "Try leaving a new issue comment over at "
                        + "[my Github page](https://github.com/jo32pilot/Shouko/issues/new)"
                        + " describing the situation. It would help a lot!")
        await bot.send_message(channel, embed=embeder)
        logger.error(context.message.content + '\n' + ''.join(traceback.format_exception(
                        type(error), error, error.__traceback__)))


#------------CHECKS------------#
# NOTE: All parameters named "context" are explained in the Discord API 
# reference.


def check_server(context):
    """Checks if server is blacklisted.
    
    Used with the discord.commands module's check decorator.

    """
    return context.message.channel.server.id not in config["server_blacklist"]



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
                        + '~help rm_ranktime\n~help rm_usertime'
                        + '\n~help toggle_messages\n~github\n~donate')
    await bot.send_message(context.message.channel, embed=embeder)

@bot.command(pass_context=True)
@commands.check(check_server)
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
@commands.check(check_server)
async def my_time(context):
    try:
        times = global_member_times[context.message.server.id]
        time = convert_from_seconds(times[context.message.author.id][0])
        await bot.say('%s Hours, %s Minutes, %s Seconds' % time)
    except KeyError as e:
        await bot.say('You haven\'t entered a voice channel in this server since '
                        + 'you last joined it! Join a voice channel to recieve your time.')

@bot.command(pass_context=True)
@commands.check(check_server)
async def leaderboard(context, amount):
    try:
        int_amount = int(amount)
    except ValueError as e:
        await bot.say('A valid number must be entered. e.g., 1, 2, 3...')
        return
    if int_amount < 1 or int_amount > 15:
        await bot.say('Sorry! I only support numbers between 1 and 15.')
        return
    server = context.message.server
    times = global_member_times[server.id]
    embeder = Embed(title=('Top %s Server Member Times' % amount), colour=16755456, type='rich')
    to_sort = dict()
    for person in times:
        to_sort.update({person:times[person][0]})
    sorted_list = sorted(to_sort, key=to_sort.get)
    thumbnail = None
    top_x = (-1 * int_amount) - 1
    for person in sorted_list[:top_x:-1]:
        try:
            top_memb = utils.find(lambda member: member.id == person, server.members)
            time_spent = convert_from_seconds(times[person][0])
            if thumbnail is None:
                thumbnail = top_memb.avatar_url
            if thumbnail == '':
                thumbnail = top_memb.default_avatar_url
            embeder.add_field(name=top_memb.name, value=
                    ('%s Hours, %s minutes, and %s seconds' % time_spent),
                    inline=False)
        except (AttributeError, ValueError, KeyError) as e:
            print(str(e))
            top_x -= 1
    embeder.set_thumbnail(url=thumbnail)
    await bot.send_message(context.message.channel, embed=embeder)
        

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def whitelist(context, *name):
    if len(name) == 0:
        bot.say('Please enter a name.')
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
@commands.check(check_server)
async def unwhitelist(context, *name):
    if len(name) == 0:
        bot.say('Please enter a name.')
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
@commands.check(check_server)
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
@commands.check(check_server)
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
@commands.check(check_server)
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
@commands.check(check_server)
async def clean_slate(context):
    times = global_member_times[context.message.server.id]
    for person in times:
        times[person][1] = 0
    await bot.say('Done!')

@bot.command(name='ranktime', pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def rank_time(context, *args):
    if len(args) < 1:
        raise commands.MissingRequiredArgument()
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
        return
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
            try:
                given_roles = [role for role in person_obj.roles if role.name not in role_orders[server_id]]
            except AttributeError as e:
                pass
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
@commands.check(check_server)
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
@commands.check(check_server)
async def rm_usertime(context, *args):
    if len(args) < 1:
        raise commands.MissingRequiredArgument()
    server_id = context.message.server.id
    user = find_user(context.message.server, args)
    if user is None:
        await bot.say("I can't find this person.")
    else:
        times = global_member_times[server_id]
        given_roles = [role for role in user.roles if role.name not in role_orders[server_id]]
        times[user.id][0] = 0
        times[user.id][1] = 0
        try:
            next_rank = role_orders[server_id][0]
            rank_time = server_configs[server_id][next_rank]
            active_threads[server_id][user.id].next_rank = next_rank
            active_threads[server_id][user.id].rank_time = rank_time
        except (KeyError, ValueError, IndexError) as e:
            pass
        await bot.replace_roles(user, *given_roles)
        await bot.say("Done!")

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
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
    error = False
    if not os.path.isfile(server.id + 'stats.txt'):
        curr_stats = open(server.id + 'stats.txt', 'w')
        global_member_times.update({server.id:dict()})
        for member in server.members:
            global_member_times[server.id].update({member.id:[0, 0]})
            #first element in list = time, second element = rank position
        return error
    elif os.stat(server.id + 'stats.txt').st_size == 0:
        global_member_times.update({server.id:dict()})
        for member in server.members:
            global_member_times[server.id].update({member.id:[0, 0]})
        return error
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
    return error

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

def check_stats_presence(member):
    server_id = member.server.id
    if member.id not in global_member_times[server_id]:
        global_member_times[server_id].update({member.id:[0, 0]})

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
        blank = ''
        semi_colon = ';'
        for key in list(server_times.keys()):
            stats.write(blank + key + '=' + str(server_times[key]))
            blank = semi_colon


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
        while (not self.member.voice.deaf and not self.member.voice.self_deaf and 
                self.member.voice.voice_channel is not None 
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
                if server.id in config["server_blacklist"]:
                    continue
                try:
                    update_stats(server)
                except KeyError as error:
                    logger.info(server.name + ''.join(traceback.format_exception(
                                type(error), error, error.__traceback__)))
                    continue
            time.sleep(config['sleep_time'])
        logging.shutdown()

bot.run(config['token'])
