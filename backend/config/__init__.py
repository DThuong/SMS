# Dùng PyMySQL thay mysqlclient nếu máy không cài được mysqlclient
try:
    import MySQLdb  # noqa: F401
except ImportError:
    try:
        import pymysql
        pymysql.install_as_MySQLdb()
    except ImportError:
        pass
