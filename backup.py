"""
Creates backups for each server's files containing user times and ranks.
Should use crontab to run on a daily basis.

Example:

    $ python3 backup.py

"""

import os
import logging
from shutil import copyfile

# ------- CONSTANTS ------- #

NEW_LOG_MESSAGE = "New files not processed and added to backups\n\t %s\n" 
OLD_LOG_MESSAGE = "Old files not processed:\n\t %s\n\n"
ERR_LOG_MESSAGE = "check_valid failed for %s\n"
BACKUP_PATH = "./backupStats/%s"
SPLIT_TIME_AND_RANK = ", "
FILE_EXT = "stats.txt"
SPLIT_ID_VALUES = '='
SPLIT_USERS_BY = ';'
EXCESS = "[\n]"
CURR_DIR = '.'

# Because we let role managers reset specific user 
# times from their servers I don't really know a better way to 
# determine whether the times were all lost, or the server
# moderators just decided to go on a mass purge, resetting
# every ones times manually. 
# GUESS denotes the amount of people whos times could have
# possibly been manually reset on any given day.
# If the number of people of people who's times were reset
# happens to be bigger than GIVEN, a backup will
# not be created.

GUESS = 3

# ------------------------- #

# -------- LOGGING -------- #

logger = logging.getLogger("backup")
logger.setLevel(logging.INFO)
handler = logging.FileHandler(filename='backup.log', 
        encoding='utf-8', mode='a')
handler.setFormatter(
        logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# ------------------------- #

def main():
    """ Main function that runs program for all specified files."""

    # Getting file names from both directores
    new_files = [new for new in os.listdir(CURR_DIR) if new.endswith(FILE_EXT)]

    # The backup directory should only have the backups but we check if
    # each file has FILE_EXT just in case
    backups = [backup for backup in os.listdir(BACKUP_PATH % CURR_DIR) 
            if backup.endswith(FILE_EXT)]

    # Shouldn't have duplicates so we don't run the risk of overwritting
    # another file.
    i = 0
    while i < len(new_files):

        # Increment accomodates for removal from both lists
        increment = True
        j = 0

        while j < len(backups):

            new = new_files[i]
            old = backups[j]

            # Check if the file names are the same
            if new == old:
                check_valid(get_dict(new), get_dict(BACKUP_PATH % old), new)

                # Remove from list so we can log what has not been touched
                new_files.remove(new)
                backups.remove(old)
                increment = False
            else:
                j += 1

        if increment == False:
            continue
        i += 1

    # Accomodates for new servers since this program was last run
    for new in new_files:
        copyfile(new, BACKUP_PATH % new)

    logger.info(NEW_LOG_MESSAGE % str(new_files))
    logger.info(OLD_LOG_MESSAGE % str(backups))


def get_dict(file_path):
    """Parses files into dictionaries to be able to compare user times.

    Args:
        file_path (string): Path to text file to parse into dictionary.

    Returns: 
        dict: Dictionary housing user ids as keys and their times as values.

    """
    id_time = dict()
    curr_stats = open(file_path, 'r')

    # Split each user as the entire file is just one line
    readable = curr_stats.read().split(SPLIT_USERS_BY)

    # Loop through each user and parse out values
    for pair in readable:
        member_id, time_and_rank = pair.split(SPLIT_ID_VALUES)

        # Rank and time were written into file as list so get rid of brackets
        time_and_rank = time_and_rank.strip(EXCESS)

        # Get rid of comman between rank and time
        time_and_rank = time_and_rank.split(SPLIT_TIME_AND_RANK)
        id_time[member_id] = int(float(time_and_rank[0]))

    curr_stats.close()
    return id_time

def check_valid(new, backup, filename):
    """Checks validity of both files.

    Compares each user's times from the more recent text file and the backup.
    If most (this is where the GUESS constant comes in) of the users times are
    greater than or equal to their backed up times, then we are safe to 
    overwrite the old backup. Otherwise log an error to check on later.

    Args:
        new (dict): Dictionary with users and times of more recent file.
        backup (dict): Dictionary with users and times of backup file.
        filename (string): Name of file currently checking.
    """
    # Tracks number of key errors
    key_errors = 0

    # Tracks number of users with backup times greater than their new times
    tracker = 0

    # Loop through each person
    for key in backup:

        # Possible room for key errors if someone exists in one file but not in
        # the other
        try:
            if new[key] >= backup[key]:
                continue
            else:
                tracker += 1
        except KeyError as e:
            key_errors += 1

    # Checks if too many people have backup times greater than their new times
    # and also the possibility that the file is just blank, which would be 
    # really really bad.
    if key_errors == len(backup) or tracker >= GUESS:
        logger.error(ERR_LOG_MESSAGE % filename)
        return
    else:
        copyfile(filename, BACKUP_PATH % filename)


if __name__ == "__main__":
    main()
