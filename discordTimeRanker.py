import asyncio
import logging
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
    embeder.add_field(name='~settup', value='Settup for ranks and commands'
                        + ' to change them')
    embeder.add_field(name='~whitelist [discord_name#****]', value='Adds '
                        + 'people to the whitelist. People on the whitelist '
                        + 'are not ranked by time. Typing "~whitelist" by '
                        + 'itself lists people on the list.')
    embeder.add_field(name='~unwhitelist [discord_name#****]', value='Removes '
                        + 'people from the whitelist. People removed will have '
                        + 'their ranks set to the lowest level.')
    await bot.send_message(context.message.channel, embed=embeder)
    logger.debug(embeder.fields)

#use time.time, then, in for loop, set delay to check, time.time - initial time.time
#update experience when someone wants to check

@bot.command(pass_context=True)
async def settup(context):
    embeder = Embed(title='Rank Settup', colour=16777215, type='rich')
    embeder.add_field(name='~frequency -m/-h/-d [amount]', value='How often '
                        + 'ranks will automatically update.\n-m/-h/-d denote ' 
                        + 'how you want time to be measured.\n-m - minutes\n'
                        + '-h - hours\n-d - days\n')
    embeder.add_field(name='~update', value='Force update ranks.', inline=False)
    await bot.send_message(context.message.channel, embed=embeder)
    logger.debug(embeder.fields)

#Helper methods/checks go below


bot.run(TOKEN)
