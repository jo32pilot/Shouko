import asyncio
import logging
import os.path
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

TOKEN = 'NDYyMzc5ODU0NjI5Njk5NTk0.DlDzOw.9WBANK1QKaK17-3dbS6_nde46QU'
bot = Bot(command_prefix='~', case_insensitve=True)

@bot.event
async def on_ready():
    await bot.change_presence(game=Game(name='by itself'))

bot.remove_command('help')
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
#update experience when someone wants to check
#on start up, get all configs and store into dictionary. {server_id:{option:value}}

@bot.command(pass_context=True)
async def settup(context):
    if not os.path.isfile(context.message.server.id + '.txt'):
        config = open(context.message.server.id + '.txt', 'w')
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
    if not os.path.isfile(context.message.server.id + '.txt'):
        config = open(context.message.server.id + '.txt', 'w')
        config.write('frequency=' + measurement + str(time) + ';')
        config.close()
    else:
        server_id = context.message.server.id
        change_config(server_id + '.txt', server_id + '.txt.tmp', 'frequency'
                        , measurement + str(time))
    

#Helper methods/classes/checks go below

def change_config(old_file, new_file, option, value):
    old_config = open(old_file, 'r')
    new_config = open(new_file, 'w+')
    old_settings = old_config.read()
    try:
        new_settings = dict(pair.split('=') for pair in old_settings.split(';'))
    except ValueError as exception:
        new_settings = {option:value}
    new_settings[option] = value
    for key, value in new_settings.items():
        new_config.write(key + '=' + str(value) + ';')
    old_config.close()
    new_config.close()
    os.remove(old_file)
    os.rename(new_file, old_file)


bot.run(TOKEN)
