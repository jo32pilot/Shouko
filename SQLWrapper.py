from mysql import connector
from mysql.connector.pooling import MySQLConnectionPool

connector.threadsafety = 1
_POOL_SIZE = 100
_INCREASE_POOL_SIZE = 50


class SQLWrapper():

    def __init__(self, config):
        self.config = config
        self._db_pool = MySQLConnectionPool(pool_name="disc_pool",
                                            pool_size=_POOL_SIZE,
                                            **config)

    def _update_size(self):

    def _assert_empty_pool(self):

    def update_all_time(self):

    def update_rank(self):
        """ 
            Also updates time

        """

    def add_to_server_config(self):
        """
            roles       | times     | _send_message
            ----------- |---------- |--------------
            role name 1 | time 1    | true
            ----------- |---------- |--------------
            role name 2 | time 2    | false
            ----------- |---------- |--------------
            ...         | ...       | ...
            ----------- |---------- |--------------
            ...         | ...       | ...
        """
        

    def remove_from_server_config(self):

    def whitelist_user(self):
        """
            Just make whitelist property a bool column in the same table
            with the times and ranks
        """

    def unwhitelist_user(self):
