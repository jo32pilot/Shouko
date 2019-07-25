"""

Defines functionality for the discord bot, Shouko. Keeps track of user time
spent in voice channels. Server members with role managing permissions can
allow the bot to assign discord roles based on accumulated time.

Example:

    $ python3 discordTimeRanker.py


Attributes:
    
    sql (SQLWrapper) Wrapper for sql python connector.
    
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
        Role is an integer representing the users next role in the role
        hierarchy organized by role orders.
        Ex.

                    Role Hierarchy: [Peasant, Craftsman, Noble, Royalty]
            Integer Representation: [      0,         1,     2,       3]

            [1000, 2] means user_id is a Craftsman with 1000 seconds spent in
            the server's voice channels.

        Note that:
            user_id is always (string)

    role_orders (dict): Holds (server_id, list) pairs where server_id indicates
        what server the list value belongs to. The list holds server roles
        ordered by their time_milestones in ascending order. This way, we
        know where each role stands in the heirarchy and can represent each
        person's role as an integer

    server_wl (dict): Holds (server_id, set) pairs where server_id indicates
        what server the list value belongs to. The set holds whitelisted 
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
from sql_wrapper import SQLWrapper

#------------CONSTANTS------------#

_HIGH_RANK = 2                # Minimum number of roles user must be above in
                              # the hierarchy for some edge cases.
                             
_HELP_COLOR = 26575           # Color to embed.
_SETTUP_COLOR = 3866383
_BOARD_COLOR = 16755456
_WHITELIST_COLOR = 16777215
_LINK_COLORS = 26575

_MAX_BOARD_SIZE = 15          # Maximum amount of people to be shown on a
                              # leaderboard.

_SECONDS = 60                 # Seconds in a minute.
_MINUTES = 60                 # Minutes in an hour.

_ID_INDEX = 0                 # Index of returned sql row where user id is
_TIME_INDEX = 1               # Index of returned sql row where user time is
_RANK_INDEX = 2               # Index of returned sql row where rank is
_WL_STATUS_INDEX = 3          # Index of returned sql row where wl_status is

#------------LOGGING------------#

#Sets up logging. Template taken from 
# discordpy.readthedocs.io/en/latest/logging.html
logger = logging.getLogger('discord')
logger.setLevel(logging.INFO)
handler = logging.FileHandler(filename='discord.log'
                            , encoding='utf-8', mode='w')
handler.setFormatter(
        logging.Formatter('%(asctime)s:%(levelname)s:%(module)s:%(lineno)d: '
                            + '%(message)s'))
logger.addHandler(handler)

#------------SETTUP / ATTRIBUTES------------#

with open('config.json', 'r') as file:
    config = json.load(file)

sql = SQLWrapper(config.db_config)
bot = Bot(command_prefix='~', case_insensitve=True)
server_configs = dict()
global_member_times = dict()
role_orders = dict()
server_wl = dict()
server_events = dict()
active_threads = dict()
bot.remove_command('help')

# Used for determining if user should be notified on role update.
# This is needed for a fatal edge case.
message_user = True

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
    await bot.change_presence(game=Game(name='#KyoaniStrong | ~help'))
    logger.info(str(server_configs))


@bot.event
async def on_server_join(server):
    """Event called when bot joins the server.

    Creates appropriate files to write server configurations and stats to.
    Sets up appropriate attribute dictionaries for the server joined. Also
    starts TimeTracker threads to start accumulating times for users 
    in voice channels upon the bot joining.

    """

    if server.name is not None:
        logger.info('Joining server ' + server.name)

    # Fills up attribute dictionaries and creates appropriate text files.
    stats_start(server)
    config_start(server)
    role_orders.update({server.id:get_roles_in_order(server)})
    active_threads.update({server.id:dict()})
    stat_event = threading.Event()
    stat_event.set()
    server_events.update({server.id:stat_event})

    # Check if people joined since bot was last on since on_ready relies on this
    # function as well.
    for person in server.members:
        check_stats_presence(person) 

    # Start TimeTrackers threads for people in voice channels.
    message_user = False
    for channel in server.channels:
        for person in channel.voice_members:
            m_voice = person.voice
            if (not m_voice.is_afk and not m_voice.deaf and 
                    not m_voice.self_deaf
                    and person.id not in active_threads[server.id]):
                new_thread = TimeTracker(server, person)
                new_thread.start()
                active_threads[server.id].update({person.id:new_thread})
    message_user = True

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
            to_send = ('If you don\'t want to recieve any of these messages '
                    + 'directly, settup a default channel in your server and '
                    + 'I\'ll send these over there!')
            await bot.send_message(reciever, content=to_send)

@bot.event
async def on_server_role_delete(role):
    """Event called when a server deletes a role.

    Updates underlying ranking structure settup in the attribute dictionaries.
    Also reassigns roles accordingly.
        
    """
    server_id = role.server.id

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
    for person in (set(times.keys()) - server_wl[server_id]):
        person_obj = utils.find(lambda member: member.id == person,
                                role.server.members)
        if person_obj == None:
            logger.error("%s: person_obj evaluated to None: %s" % 
                    (role.server.id, person))

        # given_roles is a list of a user's roles that do not have a time
        # milestone associated with them. These should be returned to the user.
        given_roles = [role for role in person_obj.roles 
                if role.name not in role_orders[server_id]]

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
        if times[person][1] - _HIGH_RANK >= 0:
            previous_rank = previous_role_orders[times[person][1] - _HIGH_RANK]
        else:
            previous_rank = None

        # If the user is the role that is being removed.
        if curr_rank is not None and curr_rank == role.name:

            # Attempt to revoke their current role and replace it with a lower
            # role or none at all (and give them back given_roles)
            try:
                await bot.replace_roles(person_obj, utils.find(
                        lambda prev_obj: (prev_obj.name == previous_rank), 
                        role.server.roles), *given_roles)

            except (discord.errors.Forbidden, AttributeError) as e:
                logger.info('%s:%s : Exception Occured' % 
                        (person_obj.name, person))
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
                next_rank_time = convert_time(
                        server_configs[server_id][next_rank])
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
        await bot.send_message(channel, "You didn't provide me enough arguments"
                            + ". Checkout out the ~help command and try again!")

    elif isinstance(error, commands.CheckFailure):
        await bot.send_message(channel, "You're missing role managing"
              + "permissions!")

    else:
        embeder = Embed(type='rich', description="Sorry! I ran into an error. "
                        + "Try leaving a new issue comment over at "
                        + "[my Github page](https://github.com/jo32pilot/Shouko"
                        + "/issues/new)"
                        + " describing the situation. It would help a lot!")
        await bot.send_message(channel, embed=embeder)
        logger.error(context.message.content + '\n' + 
                ''.join(traceback.format_exception(type(error), error, 
                error.__traceback__)))


#------------COMMANDS------------#


@bot.command(pass_context=True)
async def help(context, *cmd):
    """Sends custom help message to text channel.

    Args:
        context (Context): Described in the discord.ext.commands API referece.
        *cmd: Variable length parameter list that we only want the first
            element from. If no arguments are provided in the command,
            a help message with a list of acceptable arguments is sent.
        
    """
    embeder = Embed(colour=_HELP_COLOR, type='rich')

    # Attempts to find first argument from command.
    try:
        command = cmd[0]
        embeder.add_field(name=config[command][0], value=config[command][1])

    # Prepares default help message if could not find first argument.
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
    """Sends server's settup to view.

    The settup message consists of a list of the server's roles with their
    appropriate milestones in ascending order. If the role does not have a
    time milestone, it is not sent.

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    server_id = context.message.server.id
    to_send = ''

    # Loop prepares settup message.
    for role in role_orders[server_id][::-1]:
        to_send = (to_send + role + ': ' 
                + server_configs[server_id][role] + '\n')
    embeder = Embed(title='Rank Settup', colour=_SETTUP_COLOR, type='rich', 
            description=to_send)
    await bot.send_message(context.message.channel, embed=embeder)
    logger.debug(embeder.fields)

@bot.command(pass_context=True)
@commands.check(check_server)
async def my_time(context):
    """Tells users their total time spent in the server's voice channels.

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    try:
        times = global_member_times[context.message.server.id]
        time = convert_from_seconds(times[context.message.author.id][0])
        await bot.say('%s Hours, %s Minutes, %s Seconds' % time)
    except KeyError as e:
        await bot.say('You haven\'t entered a voice channel in this server '
                + 'since you last joined it! Join a voice channel to recieve '
                + 'your time.')

@bot.command(pass_context=True)
@commands.check(check_server)
async def leaderboard(context, amount):
    """Lists users with the most time spent in voice channels in the server.

    Args:
        context (Context): Described in the discord.ext.commands API referece.
        amount (int): Amount of people to show on the leaderboard. Max is 15.

    """
    # Check if valid argument
    try:
        int_amount = int(amount)
    except ValueError as e:
        await bot.say('A valid number must be entered. e.g., 1, 2, 3...')
        return
    if int_amount < 1 or int_amount > _MAX_BOARD_SIZE:
        await bot.say('Sorry! I only support numbers between 1 and 15.')
        return

    server = context.message.server
    times = global_member_times[server.id]
    embeder = Embed(title=('Top %s Server Member Times' % amount), 
            colour=_BOARD_COLOR, type='rich')

    # Get users sorted by their accumuluated time in ascending order
    to_sort = dict()
    for person in times:
        to_sort.update({person:times[person][0]})
    sorted_list = sorted(to_sort, key=to_sort.get)
    thumbnail = None

    # top_x denotes the index of the last user on the leaderboard (when to stop
    # looping)
    top_x = (-1 * int_amount) - 1

    # Loop backwards from sorted list of members to get people with the most
    # time.
    for person in sorted_list[:top_x:-1]:
        try:
            top_memb = utils.find(lambda member: member.id == person, 
                    server.members)
            time_spent = convert_from_seconds(times[person][0])

            # If user has a default profile picture.
            if thumbnail is None:
                thumbnail = top_memb.avatar_url

            # If user has a custom profile picture.
            if thumbnail == '':
                thumbnail = top_memb.default_avatar_url

            embeder.add_field(name=top_memb.name, value=
                    ('%s Hours, %s minutes, and %s seconds' % time_spent),
                    inline=False)
        except (AttributeError, ValueError, KeyError) as e:
            logger.error(str(e))
            top_x -= 1

    embeder.set_thumbnail(url=thumbnail)
    await bot.send_message(context.message.channel, embed=embeder)
        

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def whitelist(context, *name):
    """Adds users to a whitelist for their server.

    Users on their server's whitelist still accumulate time, but are not
    affected by the automated role assignment. This allows server moderators
    to manually assign roles with time milestones associated with them to users 
    without facing reprecussions. (Because assigning a role with a
    time milestone higher than a users accumulated time has possible
    consequences.)

    Args:
        context (Context): Described in the discord.ext.commands API referece.
        *name: Variable length parameter list that should hold parts of one 
            username as usernames can have spaces between them.

    """
    if len(name) == 0:
        await bot.say('Please enter a name.')
        return
    server = context.message.server
    to_list = find_user(server, name)

    # Usage message if we can't find the user.
    if to_list is None:
        await bot.say('Sorry! I can\'t find this person. '
                + 'Remember that the format for this command is \n\n'
                + '`~whitelist [discord_username#XXXX]` '
                + '(Names are case sensitive)'
                + '\n\nExample usage: ```~whitelist Shouko Nishimiya#1234```')

    elif to_list.id in server_wl[server.id]:
        await bot.say('Member is already on the whitelist.')

    else:
        server_wl[server.id].add(to_list.id)
        sql.whitelist_user(server.id, to_list.id)
        await bot.say('Whitelist successful!')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def unwhitelist(context, *name):
    """Removes a user from the server's whitelist.

    Upon removal from the whitlist, because they were still accumulating time
    while still on the whitelist, we need to update their roles to line up with 
    their total time. 

    Args:
        context (Context): Described in the discord.ext.commands API referece.
        *name: Variable length parameter list that should hold parts of one
            username as usernames can have spaces between them.
    """
    # First part is essentially the same as the whitelist code but with some
    # small changes.
    if len(name) == 0:
        bot.say('Please enter a name.')
    server = context.message.server
    times = global_member_times[server.id]
    to_list = find_user(server, name)
    if to_list is None:
        await bot.say('Sorry! I can\'t find this person. '
                + 'Remember that the format for this command is\n\n'
                + '`~unwhitelist [discord_username#XXXX]` '
                + '(Names are case sensitive)'
                + '\n\n Example usage: '
                + '```~unwhitelist Shouko Nishimiya#1234```')
    elif to_list.id not in server_wl[server.id]:
        await bot.say('Member is already not on the whitelist.')

    # This part updates the user's roles
    elif to_list.id in server_wl[server.id]:
        server_wl[server.id].remove(to_list.id)
        times[to_list.id][1] = 0

        try:

            # Attempt to update TimeTracker thread if one exists. TimeTracker
            # will handle the role updates.
            try:
                new_rank = role_orders[server.id][times[to_list.id][1]]
                new_rank_time = convert_time(
                        server_configs[server.id][new_rank])
                active_threads[server.id][to_list.id].next_rank = new_rank
                active_threads[server.id][to_list.id].rank_time = new_rank_time
            except IndexError as e:
                active_threads[server.id][to_list.id].rank_time = None

        except (AttributeError, KeyError) as exc:
            pass

        sql.unwhitelist_user(server.id, to_list.id)
        await bot.say('Member has been removed from the whitelist! Rank '
                        + 'should be given back after rejoining a voice '
                        + 'channel if not already returned.')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def whitelist_all(context):
    """Adds all users on the server to the whitelist.

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    server = context.message.server
    # Update server_wl dictionary.
    server_wl[server.id] = {member for member in global_member_times[server.id]}
    sql.whitelist_all(server.id)
    await bot.say('Done!')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def unwhitelist_all(context):
    """Remove all users from the server's whitelist.

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    # Almost the same as the unwhitelist command but for everyone.
    server = context.message.server
    times = global_member_times[server.id]
    server_wl[server.id] = set()
    for person in times:
        times[person][1] = 0
        try:
            try: 
                new_rank = role_orders[server.id][times[person][1]]
                new_rank_time = convert_time(
                        server_configs[server.id][new_rank])
                active_threads[server.id][person].next_rank = new_rank
                active_threads[server.id][person].rank_time = new_rank_time
            except IndexError as e:
                active_threads[server.id][person].rank_time = None
        except (AttributeError, KeyError) as exc:
            pass
    sql.unwhitelist_all(server.id)
    await bot.say('Done!')

@bot.command(pass_context=True)
@commands.check(check_server)
async def list_whitelist(context):
    """Lists all people on the server's whitelist.

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    server = context.message.server
    to_send = ''
    for person in server_wl[server.id]:
        to_list = utils.find(lambda member: member.id == person, server.members)
        to_send = '%s%s#%s\n' % (to_send, to_list.name, to_list.discriminator)
    embeder = Embed(title='Whitelist', colour=_WHITELIST_COLOR, type='rich', 
            description=to_send)
    await bot.send_message(context.message.channel, embed=embeder)

@bot.command(name='cleanslate', pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def clean_slate(context):
    """Reset underlying role system for the server.

    Sets all role integers to 0 for each person as a fix to any possible
    issues with manual assignment of roles as described before.
    (see: whitelist, server_wl) 
    Roles will be returned immediately to a user if they have a running
    TimeTracker thread or whenever they start one. (Basically if they
    are already in a voice channel or whenever they join one while not
    deafened).

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    times = global_member_times[context.message.server.id]
    for person in times:
        times[person][1] = 0
    await bot.say('Done!')

@bot.command(name='ranktime', pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def rank_time(context, *args):
    """Attaches a time milestone to a role.

    Also reassigns roles according to users' total times. (e.g. if a user has
    more time than the newly assigned milestone and the milestone is the highest
    among all others, then the user is assigned the new role.)

    Args:
        context (Context): Described in the discord.ext.commands API referece.
        *args: Variable length parameter list where the first element should be
            an existing role in the server to assign the time milestone to and
            the second element should be the time formated as hhh:mm:ss where
            hhh is hours, mm is minutes, and ss is seconds.
            Ex.
                [Peasant, 000:00:00] will assign the role Peasant to all users
                with 0 hours, 0 minutes, and 0 seconds spent in voice channels.

                (In this case, users who haven't spent any time in voice 
                channels will be assigned the role whenever they join one.)

    Raises:
        MissingRequiredArgument: If either the role or the time milestone is not
            provided.

    """
    if len(args) < 1:
        raise commands.MissingRequiredArgument()
    rank = ' '.join(args[:-1])
    time = args[-1]
    server_id = context.message.server.id
    times = global_member_times[server_id]
    count = 0

    # Search for roles with the same name.
    for role in context.message.server.roles:
        if role.name == rank:
            count += 1
            if(count > 1):
                await bot.say('Cannot change rank time if multiple ranks have '
                        + 'the same name.')
                return

    # Couldn't find role at all.
    if count == 0:
        await bot.say('Cannot find a role with the name %s.' % rank)
        return

    # Search for ranks with the same time
    for a_rank in role_orders[server_id]:
        if (server_configs[server_id][a_rank]) == time:
            await bot.say('Sorry, we do not support ranks having the same times'
                    + ' at this moment.')
            return
    new_time = convert_time(time)

    # Incorrect arg format
    if new_time == None:
        await bot.say('The formatting of your time argument is incorrect.\n' 
                        + 'Usage: `~ranktime [role_name] [hhh:mm:ss]`\n'
                        + 'Example: ```~ranktime A Cool Role 002:06:34```')
        return


    if rank in role_orders[server_id]:
        # Attempt to prevent race conditions
        server_events[server_id].clear()
        for person in active_threads[server_id]:
            while not active_threads[server_id][person].block_update:
                continue

        # We initialize deep copies of the old 
        # configuration before the updates to be able to compare previous 
        # role times and role positions in the hierarchy against the new ones.
        old_server_configs = copy.deepcopy(server_configs[server_id])
        change_config(server_id, rank, str(time))
        previous_role_orders = copy.deepcopy(role_orders[server_id])
        role_orders.update(
                {server_id:get_roles_in_order(context.message.server)})

        # Get integer role value.
        rank_after_new = role_orders[server_id].index(rank)
        rank_before_new = previous_role_orders.index(rank)
        rank_obj = utils.find(lambda role: role.name == rank, 
                context.message.server.roles)

        # For all people not on the whitelist
        for person in (set(times.keys()) - server_wl[server_id]):
            person_obj = utils.find(lambda member: member.id == person,
                                    context.message.server.members)

            if person_obj == None:
                logger.error("%s: person_obj evaluated to None: %s" % 
                        (context.message.server.id, person))
                continue

            try:

                # Get all roles without time milestones associated with them.
                given_roles = [role for role in person_obj.roles 
                        if role.name not in role_orders[server_id]]

            except AttributeError as e:
                pass

            # Gets users' current role name with time milestone prior the
            # server_config update.
            if times[person][1] - 1 >= 0:
                curr_rank = previous_role_orders[times[person][1] - 1]
                curr_rank_time = convert_time(old_server_configs[curr_rank])
                curr_rank_pos = previous_role_orders.index(curr_rank) 

            # Users might not have one so set fields to these values to skip
            # some steps.
            else:
                curr_rank = None
                curr_rank_time = -1
                curr_rank_pos = -1

            # Check if user has roles in the role hierarchy below their own.
            if times[person][1] - 2 >= 0:
                previous_rank = previous_role_orders[times[person][1] - 2]
                previous_rank_time = convert_time(
                        old_server_configs[previous_rank])

            # Skip some steps if not.
            else:
                previous_rank = None
                previous_rank_time = 0

            # If user has reached / passed new time milestone but does not yet
            # have the rank, assign them the rank.
            if  (curr_rank_time < new_time and times[person][0] >= new_time 
                    and curr_rank != rank):
                try:
                    await bot.replace_roles(person_obj, rank_obj, *given_roles)
                except discord.errors.Forbidden as e:
                    logger.info('%s:%s : Failed to update' % 
                            (person_obj.name, person))

                # Sets up next rank to attain if there is one
                if (times[person][1] < len(role_orders[server_id]) and 
                        rank_before_new > curr_rank_pos):
                    times[person][1] += 1

            # We allow roles with existing time milestones to change as well. 
            # This elif accounts for that.
            elif curr_rank is not None and curr_rank == rank:

                # If you are no longer at or beyond the required time milestone
                # for your role.
                if times[person][0] < new_time:

                    # If you are already the lowest role, just revoke the role.
                    # (And give back roles without milestones)
                    if times[person][1] - 1 == 0:
                        try:
                            await bot.replace_roles(person_obj, *given_roles)
                        except discord.errors.Forbidden as e:
                            logger.info('%s:%s : Failed to update' % 
                                    (person_obj.name, person))
                            continue

                    # Otherwise, attempt to give the user the role below theirs.
                    elif times[person][1] - 1 > 0:
                        previous_role = utils.find(lambda role: previous_rank 
                                == role.name, context.message.server.roles)
                        try:
                            await bot.replace_roles(person_obj, previous_role, 
                                    *given_roles)
                        except discord.errors.Forbidden as e:
                            logger.info('%s:%s : Failed to update' % 
                                    (person_obj.name, person))
                            continue

                    # Update user's next role to attain.
                    times[person][1] -= 1

                # If the updated role is now below a role it was previously
                # above, that means the user should recieve the previous
                # role.
                # Ex.               
                #   Before update: [Peasant, Craftsman,     Noble, Royalty]
                #                                            ^
                #                                           User
                #
                #    After update: [Peasant,     Noble, Craftsman, Royalty]
                #                                            ^
                #                                           User
                #
                # i.e., the user was a Noble, but after some shifts in time,
                # the user is now a craftsman, which is higher ranked than a
                # Noble now.
                elif (times[person][0] > new_time and 
                        previous_rank_time > new_time):

                    previous_role = utils.find(lambda role: previous_rank
                            == role.name, context.message.server.roles)
                    try:
                        await bot.replace_roles(person_obj, previous_role, 
                                *given_roles)
                    except discord.errors.Forbidden as e:
                        logger.info('%s:%s : Failed to update' % 
                                (person_obj.name, person))
                        continue

            # Might not affect current role but need to update users' role
            # integers to stay in line with role orders.
            else:

                # If the updated role was previously below the user's role
                # but is now above the user's role and the user has not reached
                # the new milestone, decrement role integer for next rank.
                if (times[person][1] > 0 and curr_rank is not None and 
                        rank_before_new < curr_rank_pos and
                        rank_after_new >= curr_rank_pos):
                    times[person][1] -= 1

                # The reverse. If the updated role was previously above
                # the user's role but is now below the user's role and the
                # user has passed the time milestonem increment the 
                # role integer for their next rank.
                elif (times[person][1] < len(role_orders[server_id]) and
                        curr_rank is not None and
                        rank_before_new > curr_rank_pos and 
                        rank_after_new <= curr_rank_pos):
                    times[person][1] += 1

            # Update TimeTracker thread fields.
            try:
                try:
                    new_rank = role_orders[server_id][times[person][1]]
                    new_rank_time = convert_time(
                            server_configs[server_id][new_rank])
                    active_threads[server_id][person].next_rank = new_rank
                    active_threads[server_id][person].rank_time = new_rank_time
                except IndexError as e:
                    active_threads[server_id][person].rank_time = None
            except (AttributeError, KeyError) as exc:
                continue
        server_events[server_id].set()

    # Otherwise, a role that previously did not have a time milestone was
    # added. This is only slightly more simple.
    else:

        # Try to prevent race conditions.
        server_events[server_id].clear()
        for person in active_threads[server_id]:
            while not active_threads[server_id][person].block_update:
                continue

        # Again, get deep copies of previous role orders to compare with the
        # updated role orders.
        old_server_configs = copy.deepcopy(server_configs[server_id])
        change_config(server_id, rank, str(time))
        previous_role_orders = copy.deepcopy(role_orders[server_id])
        role_orders.update(
                {server_id:get_roles_in_order(context.message.server)})
        rank_after_new = role_orders[server_id].index(rank)
        rank_obj = utils.find(lambda role: role.name == rank, 
                context.message.server.roles)

        # For everyone not on the whitelist
        for person in (set(times.keys()) - server_wl[server_id]):
            person_obj = utils.find(lambda member: member.id == person,
                                    context.message.server.members)

            # Get all roles without time milestones to reassign to the user.
            try:
                given_roles = [role for role in person_obj.roles 
                        if role.name not in role_orders[server_id]]
            except AttributeError as e:
                pass

            # Gets user's current role name
            if times[person][1] - 1 >= 0:
                curr_rank = previous_role_orders[times[person][1] - 1]
                curr_rank_time = convert_time(old_server_configs[curr_rank])
                curr_rank_pos = role_orders[server_id].index(curr_rank)
            else:
                curr_rank = None
                curr_rank_time = -1

            # If user's current role is below the new role and the user has
            # already reached the new role's time, assign the new role.
            if curr_rank_time < new_time and times[person][0] >= new_time:
                try:
                    await bot.replace_roles(person_obj, rank_obj, *given_roles)
                except discord.errors.Forbidden as e:
                    logger.info('%s:%s : Failed to update' % 
                            (person_obj.name, person))

                # Update next role integer
                if times[person][1] < len(role_orders[server_id]):
                    times[person][1] += 1

            # Otherwise, if the role is below the user's current role, just 
            # update next role integer.
            elif curr_rank is not None and rank_after_new < curr_rank_pos:
                times[person][1] += 1

            # Update TimeTracker thread fields if it exists.
            try:
                try:
                    new_rank = role_orders[server_id][times[person][1]]
                    new_rank_time = convert_time(
                            server_configs[server_id][new_rank])
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
    """Removes a time milestone from the specified role.

    Args:
        context (Context): Described in the discord.ext.commands API referece.
        *args: Variable length parameter list where each element is a part of 
            the role name.

    Raises:
        MissingRequiredArgument: If no arguments were input.

    """
    if len(args) < 1:
        raise commands.MissingRequiredArgument()
    server = context.message.server
    rank = ' '.join(args)
    if rank not in role_orders[server.id]:
        bot.say('Cannot find rank with the name %s.' % rank
                + 'Usage: `~rm_ranktime [role_name]`\n'
                + 'Example: ```~rm_ranktime A Cool Role```')

    # Updates user's roles.
    else:
        await on_server_role_delete(utils.find(lambda role: role.name == rank, 
                context.message.server.roles))
        await bot.say('Done!')

@bot.command(pass_context=True)
@commands.has_permissions(manage_roles=True)
@commands.check(check_server)
async def rm_usertime(context, *args):
    """Resets a user's total time to 0 seconds.

    Args:
        context (Context): Described in the discord.ext.commands API referece.
        *args: Variable length parameter list where each element is a part of
            the username.

    Raises:
        MissingRequiredArgument: If no arguments were input.

    """
    if len(args) < 1:
        raise commands.MissingRequiredArgument()

    server_id = context.message.server.id
    user = find_user(context.message.server, args)
    if user is None:
        await bot.say("I can't find this person.")

    # If user is found, reset role integer and time to 0.
    else:
        times = global_member_times[server_id]
        given_roles = [role for role in user.roles 
                if role.name not in role_orders[server_id]]
        times[user.id][0] = 0
        times[user.id][1] = 0

        # Attempt to update running TimeTracker threads.
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
    """Updates _send_messages324906 (determines where to send certain messages).

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    server_id = context.message.server.id
    change_config(server_id, "_send_messages324906", 
            not bool(server_configs[server_id]["_send_messages324906"]))

@bot.command(pass_context=True)
async def github(context):
    """Links github.

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    embeder = Embed(colour=_LINK_COLORS, type='rich', 
            description=config['github_url'])
    await bot.send_message(context.message.channel, embed=embeder)

@bot.command(pass_context=True)
async def donate(context):
    """Links donation page.

    Args:
        context (Context): Described in the discord.ext.commands API referece.

    """
    embeder = Embed(colour=_LINK_COLORS, type='rich', 
            description=config['patreon'])
    await bot.send_message(context.message.channel, embed=embeder)



#------------HELPER FUNCTIONS------------#



def stats_start(server):
    """Fills in global_member_times and server_wl dictionaries.
    
        Creates and populates a table in the database if no such table exists
        for this server.

    Args:
        server (Server): Server object described in the Discord API reference
            page. We populate global_member_times with this server.

    """
    member_times = {}
    server_wl[server.id] = set()
    results = sql.fetch_all(server.id)

    # Table didn't exist. Create it
    if results == None:
        vals = []
        for member in server.members:
            vals.append((member,))
            member_times[member.id] = [0, 0]
        sql.create_table(server.id, vals)

    # Otherwise use the results to populate global_member_times
    else:
        for result in results:
            user_id = result[_ID_INDEX]
            time = result[_TIME_INDEX]
            rank = result[_RANK_INDEX]
            if result[_WL_STATUS_INDEX] == True:
                server_wl[server.id].add(user_id)
            member_times[user_id] = [time, rank]
    global_member_times[server.id] = member_times
    



def config_start(server):
    """Fills in server_configs dictionary.

    Creates new text file with roles and their time milestones (if 
    a time milestone exists) among other possible configurations. If a text
    file already exists, the function reads from the existing text file to fill
    in server_configs.

    Args:
        server (Server): Server object described in the Discord API reference
            page. We populate server_configs with this server.

    """
    # If file does not already exist, create one.
    if not os.path.isfile(server.id + '.txt'):
        try:
            curr_config = open(server.id + '.txt', 'w+')

            # _send_messages324906 is named as such to be unique from any
            # possible role names. If this option is True, we send certain
            # messages to the server's default channel. Otherwise, send
            # messages directly to the users involved.
            settings = {"_send_messages324906":True}
            curr_config.write("_send_messages324906=True")
        except:
            raise
        finally:
            curr_config.close()

    # Otherwise a file exists so read from that into server_configs.
    else:
        try:
            curr_config = open(server.id + '.txt', 'r')
            readable = curr_config.read()
        except:
            raise
        finally:
            curr_config.close()

        settings = dict(pair.split('=') for pair in readable.split(';'))

    server_configs.update({server.id:settings})


def check_stats_presence(member):
    """Check if users are recorded in global_member_times and adds them if not.
    
    This function is called when a a new user joins while the bot is on, or when
    the bot starts up (meaning that new users joined while the bot was off).

    Args:
        member (Member): Member object described in the Discord API reference
            page. We check if this user is in global_member_times.

    """
    server_id = member.server.id
    if member.id not in global_member_times[server_id]:
        global_member_times[server_id].update({member.id:[0, 0]})


def change_config(server_id, option, value):
    """Changes a server's configuration for an option.

    Used to change values in server_configs, namely roles and their times. Also
    writes the changes to the appropriate file.

    Args:
        server_id (string): Unique id of the server to change the option for.
        option (string): Option to change value for.
        value: Value to change option to. Type varies.

    """
    settings = server_configs[server_id]
    settings[option] = value

    try:
        new_config = open(server_id + '.txt', 'w+')
        iterator = iter(settings)

        # Format first key, value pair in text file without semi-colon to
        # parse correctly later.
        first_key = next(iterator)
        new_config.write(first_key + '=' + str(settings[first_key]))

        for key in iterator:
            new_config.write(';' + key + '=' + str(settings[key]))
    except:
        raise
    finally:
        new_config.close()


def delete_config(server_id, option):
    """Removes a server's option, value pair in their configuration.
    
    Also writes change to the appropriate file.

    Args:
        server_id (string): Unique id of the server to change the option for.
        option (string): Option to remove.

    """
    settings = server_configs[server_id]
    del settings[option]

    try:
        new_config = open(server_id + '.txt', 'w+')
        iterator = iter(settings)
        first_key = next(iterator)
        # Format first key, value pair in text file without semi-colon to
        # parse correctly later.
        new_config.write(first_key + '=' + str(settings[first_key]))
        for key in iterator:
            new_config.write(';' + key + '=' + str(settings[key]))
    except:
        raise
    finally:
        new_config.close()


def convert_time(time):
    """Converts hhh:mm:ss to seconds.

    hhh represents hours, mm represents minutes, ss represents seconds.

    Args:
        time (string): Time to parse into seconds.

    Returns:
        None: If the total seconds is less than 0 (somehow) or a ValueError
            occurs.
        int: Returns total seconds if successful and positive.

    """
    try:
        hours, minutes, seconds = time.split(':')
        hours = int(hours) * _MINUTES * _SECONDS
        minutes = int(minutes) * _SECONDS
        seconds = int(seconds)
        final_time = hours + minutes + seconds
        if final_time < 0:
            return None
        return final_time
    except ValueError as e:
        return None


def get_roles_in_order(server):
    """Returns a list of role names sorted by their time.

    Args:
        server (Server): Server object described in the Discord API reference
            page. We get the unique id of the server from this object.

    Returns:
        list: A list of a role names sorted by their time.

    """
    to_sort = dict()

    # Put roles and time in a temporary dictionary to be able to sort into a
    # list. In hindsight, I might as well have put _send_message329406 in
    # another dictionary and text file because this is super ineffecient to do.
    for role in server.roles:
        try:
            to_sort.update({role.name:convert_time(
                    server_configs[server.id][role.name])})
        except KeyError as e:
            continue

    return sorted(to_sort, key=to_sort.get)


def update_stats(server):
    """Writes updated user times to the appropriate text file.

    Args:
        server (Server): Server object described in the Discord API reference
            page. We get the unique id of the server from this object.

    """
    with open(server.id + 'stats.txt', 'w') as stats:
        server_times = global_member_times[server.id]

        # blank and semi_colon are so we can parse the file correctly.
        blank = ''
        semi_colon = ';'
        for key in list(server_times.keys()):
            stats.write(blank + key + '=' + str(server_times[key]))
            blank = semi_colon


def find_user(server, name_list):
    """Parses list for a username and finds that user.

    Args:
        server (Server): Server object described in the Discord API reference
            page. We get the unique id of the server from this object.
        name_list (list): List to parse user for. Comes from command functions 
            with variable length parameter lists.

    Returns:
        None: If user could not be found, discriminator is not valid, or 
            ValueError was encountered.
        Member: Returns member object if user could be found.

    """

    # Used to find last 4 numbers in dicord username
    discrim_pattern = r"[0-9]{4}"

    try:

        # Seperates actual name from discriminator.
        last_name, discrim = name_list[-1].split('#')

        # Check for valid discriminator.
        if re.match(discrim_pattern, discrim):
            username = ' '
            username = username.join(name_list[:-1])
            if username == '':
                username = last_name
            else:
                username = username + ' ' + last_name
            to_list = utils.find((lambda person: person.name == username and
                    person.discriminator == discrim), server.members)

            # Checks if found user.
            if to_list is None:
                return None
            else:
                return to_list
        else:
            return None
    except (IndexError, ValueError) as e:
        return None


def convert_from_seconds(time):
    """Converts seconds to a tuple of hours, minutes, and seconds.

    Args:
        time: Seconds to convert to the new format. Type could be float or int.

    Returns:
        A tuple formatted as (hours, minutes, seconds) all as strings.
    """
    time = int(time)
    seconds = (time % _SECONDS)
    minutes = int(((time - seconds) / _SECONDS) % _MINUTES)
    hours = int((time - (minutes * _MINUTES) - seconds) / _SECONDS / _MINUTES)
    return (str(hours), str(minutes), str(seconds))



#------------THREADING CLASSES------------#



class TimeTracker(threading.Thread):
    """Threading class that tracks user time in voice channels.

    This class is a means to track all user times seperatley while assigning
    roles accordingly.

    Attributes:
        server (Server): Server object described in the Discord API reference.
            Used to find out what server this user belongs to.

        member (Member): Member object described in the Discord API reference.
            This is the user to start the thread for.

        member_time: Total time spent in the server's voice channels. Type can
            be float or int.

        bot_in_server (bool): True if bot is in the server. False otherwise.

        stat_event (Event): Threading module's Event object to prevent race
            conditions.

        block_update (bool): Begins as False and constantly changes. Used
            to try to prevent race conditions.

        next_rank (int): Role integer of next role for user to attain.

        rank_time (int): Time required to achieve next_rank.

    """

    def __init__(self, server, member):
        """Initializes TimeTracker instance the specified user.

        Args:
            server (Server): Server object described in the Discord API 
                reference. Used to find out what server this user belongs to.
            member (Member): Member object described in the Discord API
                reference. This is the user to start the thread for.

        """
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
            self.rank_time = convert_time(
                    server_configs[server.id][self.next_rank])

        # If user is already the highest role, set to None.
        except IndexError as e:
            self.rank_time = None

    def run(self):
        """Starts thread to begin tracking user's time.

        Also updates roles if user has reached a time milestone.

        """
        now = time.time()
        times = global_member_times[self.server.id]

        # While the user is not deafened, not afk, and the bot is in the server.
        while (not self.member.voice.deaf and 
                not self.member.voice.self_deaf and 
                self.member.voice.voice_channel is not None and 
                not self.member.voice.is_afk and self.bot_in_server):

            times[self.member.id][0] = self.member_time + time.time() - now

            # Other functions possibly updating some data, so wait for those to
            # finish first.
            self.block_update = True
            self.stat_event.wait()
            self.block_update = False

            # If the user is not whitelisted and has reached a time milestone.
            if (self.member.id not in server_wl[self.server.id] and 
                    self.rank_time is not None and 
                    self.member_time + time.time() >= self.rank_time + now):

                given_roles = [role for role in self.member.roles 
                        if role.name not in role_orders[self.server.id]]

                try:
                    # This is so we can update roles from this seperate thread 
                    # which wouldn't be possible otherwise as replace_roles is 
                    # a coroutine.
                    future = asyncio.run_coroutine_threadsafe(
                            bot.replace_roles(self.member, utils.find(
                            lambda role: role.name == self.next_rank
                            , self.server.roles), *given_roles), bot.loop)
                    future.result()
                except discord.errors.Forbidden as e:
                    logger.error(str(e))

                times[self.member.id][1] += 1

                if(message_user):

                    hours, minutes, seconds = convert_from_seconds(
                            times[self.member.id][0])
                    reciever = self.server.default_channel
                    fmt_tup = (self.member.mention, hours, minutes, seconds,
                                    self.next_rank)
                    message = ("Congratulations %s! You've spent a total of "
                                + "in %s hours, %s minutes, and %s seconds in "
                                + "this server's voice channels and have "
                                + "therefore earned the rank of %s!") % fmt_tup

                    # Sends to server's default text channel if evaluates true.
                    if (reciever is not None and 
                            reciever.type == ChannelType.text and
                            bool(server_configs[self.server.id]
                            ["_send_messages324906"])):

                        future = asyncio.run_coroutine_threadsafe(
                                bot.send_message(reciever, message), bot.loop)

                    # Otherwise send message to the user that this thread belongs 
                    # to
                    else:
                        future = asyncio.run_coroutine_threadsafe(
                                bot.send_message(self.member, message), 
                                bot.loop)
                    future.result()

                # Prepare user's next role to reach.
                try:
                    self.next_rank = (role_orders[self.server.id]
                            [times[self.member.id][1]])
                    self.rank_time = convert_time(
                            server_configs[self.server.id][self.next_rank])
                    rank_time = self.rank_time + now
                except IndexError as e:
                    self.rank_time = None

                # Write updates to appropriate text file.
                update_stats(self.server)


class PeriodicUpdater(threading.Thread):
    """Updates database periodically.

    Writes updated user times and role integers to server files with stats.txt
    extension.

    """

    def __init__(self):
        """Initializes thread."""

        super().__init__()


    def run(self):
        """Constantly updates stats.txt files while the main thread is alive."""

        while threading.main_thread().is_alive():
            for server in bot.servers:
                if server.id in config["server_blacklist"]:
                    continue
                try:
                    update_stats(server)
                except KeyError as error:
                    logger.info(server.name + ''.join(
                            traceback.format_exception(
                            type(error), error, error.__traceback__)))
                    continue

        logging.shutdown()

bot.run(config['test_token'])
