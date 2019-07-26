"""
Defines wrapper class for sql python connector.

"""

from mysql import connector
from mysql.connector.pooling import MySQLConnectionPool

connector.threadsafety = 1
logger = getLogger("discord")
_POOL_SIZE = 100


class SQLWrapper():
    """Wrapper class for sql python connector.

    Defines multiple program specfic queries for convenience. Not all are
    currently in use.

    Attributes:
        _config (dict): Connection configuration for database.

        _db_pool (MySQLConnectionPool): Connection pool to the database.

    """

    def __init__(self, config):
        """Constructor to initialize the connection pool.
            
        Args:
            config (dict): Connection configuration for database.

        """
        self._config = config
        self._db_pool = MySQLConnectionPool(pool_name="disc_pool",
                                            pool_size=_POOL_SIZE,
                                            **config)


    def _get_connection(self):
        """Private helper method to get connection to the databse.
        
        Returns:
            MySQLConnection: Connection object to the database.
        """
        try:
            return self._db_pool.get_connection()
        except PoolError as e:
            logger.warning("POOL LIMIT REACHED")
            return connector.connect(**(self._config))

    def _clean_up(self, cnx, cursor):
        """Private helper method to finish up task and release connections
            
        Args:
            cnx (MySQLConnection): Connection to the database.
            cursor (MySQLCursor): Cursor object from cnx.
        """
        cnx.commit()
        cursor.close()
        cnx.close()

    def _update_query(f):
        """Decorator to help execuate database updates
            
        Args:
            f (function): Function that returns an sql query as a string.

        Returns:
            function: Function that executes the query.
        """
        def query_execute(*args):
            """Executes database queries

            Args:
                *args: Variable length parameter list with query information.
                
            """
            cnx = args[0]._get_connection()
            cursor = cnx.cursor()
            query = f(*args)
            cursor.execute(query, args[1:])
            self._clean_up(cnx, cursor)
        return query_execute

    def _fetch_query(f):
        """Decorator to help execuate database fetches
            
        Args:
            f (function): Function that returns an sql query as a string.

        Returns:
            function: Function that executes the query.
        """
        def query_execute(*args):
            """Executes database queries

            Args:
                *args: Variable length parameter list with query information.

            Returns:
                list: Fetched data.
                
            """
            cnx = args[0]._get_connection()
            cursor = cnx.cursor()
            query = f(*args)
            try:
                cursor.execute(query, args[1:])
                result = cursor.fetchall()
            except connector.error.ProgrammingError as e:
                return None
            finally:
                cursor.close()
                cnx.close()
            return result
        return query_execute


    def create_table(self, server_id, vals):
        """Creates and populates table in database for the given server.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.
            vals (list): List of values to insert into the database.

        """
        cnx = self._get_connection()
        cursor = cnx.cursor()
        query = ("CREATE TABLE %s (id VARCHAR(25) PRIMARY KEY, "
                    "time INT DEFAULT 0, rank INT DEFAULT 0"
                    "wl_status BOOLEAN DEFAULT false)")
        cursor.execute(query, server.id)
        query = "INSERT INTO %s (id) VALUES (%s)"
        cursor.executemany(query, vals)
        self._clean_up(cnx, cursor)


    def update_server(self, server_id, server_times):
        """Updates a server's respective table with new values.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.
            server_time (dict): Dictionary with new values.

        """
        cnx = self._get_connection()
        cursor = cnx.cursor()
        query = "UPDATE $s SET time=%s, rank=%s WHERE id=%s"
        for member in server_times:
            time = server_times[member][0]
            rank = server_times[member][1]
            cursor.execute(query, (server_id, time, rank, member))
        self._clean_up(cnx, cursor)

        
    @_update_query
    def add_user(self, server_id, user_id):
        """Adds user to the specified server's table.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.
            user_id (string): Unique identifier for the user whose values are
                    being updated.

        Returns:
            string: MySQL query that represents this function's use.

        """
        return "INSERT INTO %s VALUES (%s, 0, 0, false)"


    @_update_query
    def update_user(self, server_id, user_id, time, rank):
        """Updates table values for specified user.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.
            user_id (string): Unique identifier for the user whose values are
                    being updated.
            time (int): User's total accumulated time.
            rank (int): Integer representation of user's rank.

        Returns:
            string: MySQL query that represents this function's use.

        """
        return "UPDATE %s SET time=%s, rank=%s WHERE id=%s"

    @_update_query
    def whitelist_user(self, server_id, user_id):
        """Whitelists a specified user by making their whitelist status true.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.
            user_id (string): Unique identifier for the user whose values are
                    being updated.

        Returns:
            string: MySQL query that represents this function's use.

        """
        return "UPDATE %s SET wl_status=true WHERE id=%s"

    @_update_query
    def unwhitelist_user(self, server_id, user_id):
        """UnWhitelists a specified user by making their whitelist status false.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.
            user_id (string): Unique identifier for the user whose values are
                    being updated.

        Returns:
            string: MySQL query that represents this function's use.

        """
        return "UPDATE %s SET wl_status=false WHERE id=%s"

    @_update_query
    def whitelist_all(self, server_id):
        """Whitelists all users.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.

        """
        return "UPDATE %s SET wl_status=true"

    @_update_query
    def unwhitelist_all(self, server_id):
        """Unwhitelists all users.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.

        """
        return "UPDATE %s SET wl_status=false"

    @_fetch_query
    def fetch_user(self, server_id, user_id):
        """Gets a specified user's data.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.
            user_id (string): Unique identifier for the user whose values are
                    being updated.

        Returns:
            string: MySQL query that represents this function's use.

        """
        return "SELECT * FROM %s WHERE id=%s"

    @_fetch_query
    def fetch_all(self, server_id):
        """Gets all users' data for the specified server.

        Args:
            server_id (string): Unique identifier for the server whose table
                    is being created.

        Returns:
            string: MySQL query that represents this function's use.

        """
        return "SELECT * FROM %s"
