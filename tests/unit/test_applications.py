from logging import Logger
from unittest.mock import AsyncMock, Mock, call

from pytest import fixture, mark

from xiao_asgi.applications import Xiao
from xiao_asgi.routing import HttpRoute


@mark.asyncio
class TestXiao:
    @fixture
    def routes(self):
        return [
            HttpRoute("/"),
            HttpRoute("/test"),
        ]

    @fixture
    def app(self, routes):
        return Xiao(routes)

    @fixture
    def scope(self):
        return {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": "127.0.0.1",
            "root_path": "/",
            "path": "/",
            "query_string": "",
        }

    def test_create_without_routes(self):
        app = Xiao()
        assert app._routes == []

    def test_create_with_routes(self, routes):
        app = Xiao(routes)
        assert isinstance(app.logger, Logger)
        assert app._routes == routes

    async def test_calling_with_unknown_endpoint(self, app, scope):
        scope["path"] = "/invalid"
        send = AsyncMock()

        await app(scope, AsyncMock(), send)

        send.assert_has_awaits(
            [
                call(
                    {
                        "type": "http.response.start",
                        "status": 404,
                        "headers": [],
                    }
                ),
                call(
                    {
                        "type": "http.response.body",
                        "body": b"Not Found",
                        "more_body": False,
                    }
                ),
            ]
        )

    async def test_calling_with_endpoint_error(self, app, scope):
        app.logger = Mock()
        app._routes[0] = AsyncMock(side_effect=Exception())
        app._routes[0].path = "/"

        await app(scope, AsyncMock(), AsyncMock())

        app.logger.exception.assert_called_once()

    async def test_calling_with_no_endpoint_error(self, app, scope):
        send = AsyncMock()

        await app(
            scope, AsyncMock(return_value={"type": "http.request"}), send
        )

        send.assert_has_calls(
            [
                call(
                    {
                        "type": "http.response.start",
                        "status": 405,
                        "headers": [],
                    }
                ),
                call(
                    {
                        "type": "http.response.body",
                        "body": b"Method Not Allowed",
                        "more_body": False,
                    }
                ),
            ]
        )
