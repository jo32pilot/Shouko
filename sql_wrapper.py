from mysql import connector
from mysql.connector.pooling import MySQLConnectionPool

connector.threadsafety = 1
logger = getLogger("discord")
_POOL_SIZE = 100


class SQLWrapper():

    def __init__(self, config):
        self._config = config
        self._db_pool = MySQLConnectionPool(pool_name="disc_pool",
                                            pool_size=_POOL_SIZE,
                                            **config)


    def _get_connection(self):
        try:
            return self._db_pool.get_connection()
        except PoolError as e:
            logger.warning("POOL LIMIT REACHED")
            return connector.connect(**(self._config))

    def _clean_up(self, cnx, cursor):
        cnx.commit()
        cursor.close()
        cnx.close()

    def _update_query(f):
        def query_execute(*args):
            cnx = args[0]._get_connection()
            cursor = cnx.cursor()
            query = f(*args)
            cursor.execute(query, args[1:])
            self._clean_up(cnx, cursor)
        return query_execute

    def _fetch_query(f):
        def query_execute(*args):
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
        cnx = self._get_connection()
        cursor = cnx.cursor()
        query = ("CREATE TABLE %s (id VARCHAR(25) PRIMARY KEY, "
                    "time INT DEFAULT 0, rank INT DEFAULT 0"
                    "wl_status BOOLEAN DEFAULT false)")
        cursor.execute(query, server.id)
        query = "INSERT INTO %s (id) VALUES (%s)"
        cursor.executemany(query, vals)
        self._clean_up(cnx, cursor)


        
    @_update_query
    def add_user(self, server_id, user_id):
        return "INSERT INTO %s VALUES (%s, 0, 0, false)"


    # Called on thread destruction, rank update, and periodically only for
    # users with threads. User user id, not name
    @_update_query
    def update_user(self, server_id, user_id, time, rank):
        return "UPDATE %s SET time=%s, rank=%s WHERE id=%s"

    @_update_query
    def whitelist_user(self, server_id, user_id):
        return "UPDATE %s SET wl_status=true WHERE id=%s"

    @_update_query
    def unwhitelist_user(self, server_id, user_id):
        return "UPDATE %s SET wl_status=false WHERE id=%s"

    @_update_query
    def whitelist_all(self, server_id):
        return "UPDATE %s SET wl_status=true"

    @_update_query
    def unwhitelist_all(self, server_id):
        return "UPDATE %s SET wl_status=false"

    @_fetch_query
    def fetch_user(self, server_id, user_id):
        return "SELECT * FROM %s WHERE id=%s"

    @_fetch_query
    def fetch_all(self, server_id):
        return "SELECT * FROM %s"
