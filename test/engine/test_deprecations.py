import sqlalchemy as tsa
from sqlalchemy import column
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import ForeignKey
from sqlalchemy import func
from sqlalchemy import Integer
from sqlalchemy import literal
from sqlalchemy import MetaData
from sqlalchemy import pool
from sqlalchemy import select
from sqlalchemy import String
from sqlalchemy import testing
from sqlalchemy import text
from sqlalchemy import TypeDecorator
from sqlalchemy.engine.base import Engine
from sqlalchemy.engine.mock import MockConnection
from sqlalchemy.testing import assert_raises
from sqlalchemy.testing import assert_raises_message
from sqlalchemy.testing import engines
from sqlalchemy.testing import eq_
from sqlalchemy.testing import fixtures
from sqlalchemy.testing import is_false
from sqlalchemy.testing import is_true
from sqlalchemy.testing.mock import Mock
from sqlalchemy.testing.schema import Column
from sqlalchemy.testing.schema import Table


class SomeException(Exception):
    pass


class CreateEngineTest(fixtures.TestBase):
    def test_strategy_keyword_mock(self):
        def executor(x, y):
            pass

        with testing.expect_deprecated(
            "The create_engine.strategy keyword is deprecated, and the "
            "only argument accepted is 'mock'"
        ):
            e = create_engine(
                "postgresql://", strategy="mock", executor=executor
            )

        assert isinstance(e, MockConnection)

    def test_strategy_keyword_unknown(self):
        with testing.expect_deprecated(
            "The create_engine.strategy keyword is deprecated, and the "
            "only argument accepted is 'mock'"
        ):
            assert_raises_message(
                tsa.exc.ArgumentError,
                "unknown strategy: 'threadlocal'",
                create_engine,
                "postgresql://",
                strategy="threadlocal",
            )


class TransactionTest(fixtures.TestBase):
    __backend__ = True

    @classmethod
    def setup_class(cls):
        metadata = MetaData()
        cls.users = Table(
            "query_users",
            metadata,
            Column("user_id", Integer, primary_key=True),
            Column("user_name", String(20)),
            test_needs_acid=True,
        )
        cls.users.create(testing.db)

    def teardown(self):
        testing.db.execute(self.users.delete()).close()

    @classmethod
    def teardown_class(cls):
        cls.users.drop(testing.db)

    def test_transaction_container(self):
        users = self.users

        def go(conn, table, data):
            for d in data:
                conn.execute(table.insert(), d)

        with testing.expect_deprecated(
            r"The Engine.transaction\(\) method is deprecated"
        ):
            testing.db.transaction(
                go, users, [dict(user_id=1, user_name="user1")]
            )

        with testing.db.connect() as conn:
            eq_(conn.execute(users.select()).fetchall(), [(1, "user1")])
        with testing.expect_deprecated(
            r"The Engine.transaction\(\) method is deprecated"
        ):
            assert_raises(
                tsa.exc.DBAPIError,
                testing.db.transaction,
                go,
                users,
                [
                    {"user_id": 2, "user_name": "user2"},
                    {"user_id": 1, "user_name": "user3"},
                ],
            )
        with testing.db.connect() as conn:
            eq_(conn.execute(users.select()).fetchall(), [(1, "user1")])


class HandleInvalidatedOnConnectTest(fixtures.TestBase):
    __requires__ = ("sqlite",)

    def setUp(self):
        e = create_engine("sqlite://")

        connection = Mock(get_server_version_info=Mock(return_value="5.0"))

        def connect(*args, **kwargs):
            return connection

        dbapi = Mock(
            sqlite_version_info=(99, 9, 9),
            version_info=(99, 9, 9),
            sqlite_version="99.9.9",
            paramstyle="named",
            connect=Mock(side_effect=connect),
        )

        sqlite3 = e.dialect.dbapi
        dbapi.Error = (sqlite3.Error,)
        dbapi.ProgrammingError = sqlite3.ProgrammingError

        self.dbapi = dbapi
        self.ProgrammingError = sqlite3.ProgrammingError


class HandleErrorTest(fixtures.TestBase):
    __requires__ = ("ad_hoc_engines",)
    __backend__ = True

    def tearDown(self):
        Engine.dispatch._clear()
        Engine._has_events = False

    def test_legacy_dbapi_error(self):
        engine = engines.testing_engine()
        canary = Mock()

        with testing.expect_deprecated(
            r"The ConnectionEvents.dbapi_error\(\) event is deprecated"
        ):
            event.listen(engine, "dbapi_error", canary)

        with engine.connect() as conn:
            try:
                conn.execute("SELECT FOO FROM I_DONT_EXIST")
                assert False
            except tsa.exc.DBAPIError as e:
                eq_(canary.mock_calls[0][1][5], e.orig)
                eq_(canary.mock_calls[0][1][2], "SELECT FOO FROM I_DONT_EXIST")

    def test_legacy_dbapi_error_no_ad_hoc_context(self):
        engine = engines.testing_engine()

        listener = Mock(return_value=None)
        with testing.expect_deprecated(
            r"The ConnectionEvents.dbapi_error\(\) event is deprecated"
        ):
            event.listen(engine, "dbapi_error", listener)

        nope = SomeException("nope")

        class MyType(TypeDecorator):
            impl = Integer

            def process_bind_param(self, value, dialect):
                raise nope

        with engine.connect() as conn:
            assert_raises_message(
                tsa.exc.StatementError,
                r"\(.*SomeException\) " r"nope\n\[SQL\: u?SELECT 1 ",
                conn.execute,
                select([1]).where(column("foo") == literal("bar", MyType())),
            )
        # no legacy event
        eq_(listener.mock_calls, [])

    def test_legacy_dbapi_error_non_dbapi_error(self):
        engine = engines.testing_engine()

        listener = Mock(return_value=None)
        with testing.expect_deprecated(
            r"The ConnectionEvents.dbapi_error\(\) event is deprecated"
        ):
            event.listen(engine, "dbapi_error", listener)

        nope = TypeError("I'm not a DBAPI error")
        with engine.connect() as c:
            c.connection.cursor = Mock(
                return_value=Mock(execute=Mock(side_effect=nope))
            )

            assert_raises_message(
                TypeError, "I'm not a DBAPI error", c.execute, "select "
            )
        # no legacy event
        eq_(listener.mock_calls, [])


def MockDBAPI():  # noqa
    def cursor():
        return Mock()

    def connect(*arg, **kw):
        def close():
            conn.closed = True

        # mock seems like it might have an issue logging
        # call_count correctly under threading, not sure.
        # adding a side_effect for close seems to help.
        conn = Mock(
            cursor=Mock(side_effect=cursor),
            close=Mock(side_effect=close),
            closed=False,
        )
        return conn

    def shutdown(value):
        if value:
            db.connect = Mock(side_effect=Exception("connect failed"))
        else:
            db.connect = Mock(side_effect=connect)
        db.is_shutdown = value

    db = Mock(
        connect=Mock(side_effect=connect), shutdown=shutdown, is_shutdown=False
    )
    return db


class PoolTestBase(fixtures.TestBase):
    def setup(self):
        pool.clear_managers()
        self._teardown_conns = []

    def teardown(self):
        for ref in self._teardown_conns:
            conn = ref()
            if conn:
                conn.close()

    @classmethod
    def teardown_class(cls):
        pool.clear_managers()

    def _queuepool_fixture(self, **kw):
        dbapi, pool = self._queuepool_dbapi_fixture(**kw)
        return pool

    def _queuepool_dbapi_fixture(self, **kw):
        dbapi = MockDBAPI()
        return (
            dbapi,
            pool.QueuePool(creator=lambda: dbapi.connect("foo.db"), **kw),
        )


class ExplicitAutoCommitDeprecatedTest(fixtures.TestBase):

    """test the 'autocommit' flag on select() and text() objects.

    Requires PostgreSQL so that we may define a custom function which
    modifies the database. """

    __only_on__ = "postgresql"

    @classmethod
    def setup_class(cls):
        global metadata, foo
        metadata = MetaData(testing.db)
        foo = Table(
            "foo",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("data", String(100)),
        )
        metadata.create_all()
        testing.db.execute(
            "create function insert_foo(varchar) "
            "returns integer as 'insert into foo(data) "
            "values ($1);select 1;' language sql"
        )

    def teardown(self):
        foo.delete().execute().close()

    @classmethod
    def teardown_class(cls):
        testing.db.execute("drop function insert_foo(varchar)")
        metadata.drop_all()

    def test_explicit_compiled(self):
        conn1 = testing.db.connect()
        conn2 = testing.db.connect()
        with testing.expect_deprecated(
            "The select.autocommit parameter is deprecated"
        ):
            conn1.execute(select([func.insert_foo("data1")], autocommit=True))
        assert conn2.execute(select([foo.c.data])).fetchall() == [("data1",)]
        with testing.expect_deprecated(
            r"The SelectBase.autocommit\(\) method is deprecated,"
        ):
            conn1.execute(select([func.insert_foo("data2")]).autocommit())
        assert conn2.execute(select([foo.c.data])).fetchall() == [
            ("data1",),
            ("data2",),
        ]
        conn1.close()
        conn2.close()

    def test_explicit_text(self):
        conn1 = testing.db.connect()
        conn2 = testing.db.connect()
        with testing.expect_deprecated(
            "The text.autocommit parameter is deprecated"
        ):
            conn1.execute(
                text("select insert_foo('moredata')", autocommit=True)
            )
        assert conn2.execute(select([foo.c.data])).fetchall() == [
            ("moredata",)
        ]
        conn1.close()
        conn2.close()


class DeprecatedEngineFeatureTest(fixtures.TablesTest):
    __backend__ = True

    @classmethod
    def define_tables(cls, metadata):
        cls.table = Table(
            "exec_test",
            metadata,
            Column("a", Integer),
            Column("b", Integer),
            test_needs_acid=True,
        )

    def _trans_fn(self, is_transaction=False):
        def go(conn, x, value=None):
            if is_transaction:
                conn = conn.connection
            conn.execute(self.table.insert().values(a=x, b=value))

        return go

    def _trans_rollback_fn(self, is_transaction=False):
        def go(conn, x, value=None):
            if is_transaction:
                conn = conn.connection
            conn.execute(self.table.insert().values(a=x, b=value))
            raise SomeException("breakage")

        return go

    def _assert_no_data(self):
        eq_(
            testing.db.scalar(
                select([func.count("*")]).select_from(self.table)
            ),
            0,
        )

    def _assert_fn(self, x, value=None):
        eq_(testing.db.execute(self.table.select()).fetchall(), [(x, value)])

    def test_transaction_engine_fn_commit(self):
        fn = self._trans_fn()
        with testing.expect_deprecated(r"The Engine.transaction\(\) method"):
            testing.db.transaction(fn, 5, value=8)
        self._assert_fn(5, value=8)

    def test_transaction_engine_fn_rollback(self):
        fn = self._trans_rollback_fn()
        with testing.expect_deprecated(
            r"The Engine.transaction\(\) method is deprecated"
        ):
            assert_raises_message(
                Exception, "breakage", testing.db.transaction, fn, 5, value=8
            )
        self._assert_no_data()

    def test_transaction_connection_fn_commit(self):
        fn = self._trans_fn()
        with testing.db.connect() as conn:
            with testing.expect_deprecated(
                r"The Connection.transaction\(\) method is deprecated"
            ):
                conn.transaction(fn, 5, value=8)
            self._assert_fn(5, value=8)

    def test_transaction_connection_fn_rollback(self):
        fn = self._trans_rollback_fn()
        with testing.db.connect() as conn:
            with testing.expect_deprecated(r""):
                assert_raises(Exception, conn.transaction, fn, 5, value=8)
        self._assert_no_data()


class DeprecatedReflectionTest(fixtures.TablesTest):
    @classmethod
    def define_tables(cls, metadata):
        Table(
            "user",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("name", String(50)),
        )
        Table(
            "address",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("user_id", ForeignKey("user.id")),
            Column("email", String(50)),
        )

    def test_exists(self):
        dont_exist = Table("dont_exist", MetaData())
        with testing.expect_deprecated(
            r"The Table.exists\(\) method is deprecated"
        ):
            is_false(dont_exist.exists(testing.db))

        user = self.tables.user
        with testing.expect_deprecated(
            r"The Table.exists\(\) method is deprecated"
        ):
            is_true(user.exists(testing.db))

    def test_create_drop_explicit(self):
        metadata = MetaData()
        table = Table("test_table", metadata, Column("foo", Integer))
        for bind in (testing.db, testing.db.connect()):
            for args in [([], {"bind": bind}), ([bind], {})]:
                metadata.create_all(*args[0], **args[1])
                with testing.expect_deprecated(
                    r"The Table.exists\(\) method is deprecated"
                ):
                    assert table.exists(*args[0], **args[1])
                metadata.drop_all(*args[0], **args[1])
                table.create(*args[0], **args[1])
                table.drop(*args[0], **args[1])
                with testing.expect_deprecated(
                    r"The Table.exists\(\) method is deprecated"
                ):
                    assert not table.exists(*args[0], **args[1])

    def test_create_drop_err_table(self):
        metadata = MetaData()
        table = Table("test_table", metadata, Column("foo", Integer))

        with testing.expect_deprecated(
            r"The Table.exists\(\) method is deprecated"
        ):
            assert_raises_message(
                tsa.exc.UnboundExecutionError,
                (
                    "Table object 'test_table' is not bound to an Engine or "
                    "Connection."
                ),
                table.exists,
            )

    def test_engine_has_table(self):
        with testing.expect_deprecated(
            r"The Engine.has_table\(\) method is deprecated"
        ):
            is_false(testing.db.has_table("dont_exist"))

        with testing.expect_deprecated(
            r"The Engine.has_table\(\) method is deprecated"
        ):
            is_true(testing.db.has_table("user"))

    def test_engine_table_names(self):
        metadata = self.metadata

        with testing.expect_deprecated(
            r"The Engine.table_names\(\) method is deprecated"
        ):
            table_names = testing.db.table_names()
        is_true(set(table_names).issuperset(metadata.tables))
