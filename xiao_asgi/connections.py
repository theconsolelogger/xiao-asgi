from abc import ABC, abstractmethod
from collections.abc import Coroutine, Generator
from typing import Any

from xiao_asgi.requests import Request
from xiao_asgi.responses import Response


class ProtocolUnknown(Exception):
    pass


class ProtocolMismatch(Exception):
    pass


class TypeMismatch(Exception):
    pass


class InvalidConnectionState(Exception):
    pass


class Connection(ABC):
    """A connection from a client to the application.

    Can be extend to represent a connection that uses a specific protocol.

    Attributes:
        protocol (str): name of the connection protocol.
    """

    protocol: str

    def __init__(
        self,
        scope: dict,
        receive: Coroutine[dict, None, None],
        send: Coroutine[dict, None, None],
    ):
        """Establish the connection information.

        This includes Coroutines that are able to receive requests from the
        client and send responses to the client.

        Args:
            scope (dict): the connection information.
            receive (Coroutine[dict, None, None]): Coroutine that is awaited to receive an incoming request.
            send (Coroutine[dict, None, None]): Coroutine that is awaited to send responses.
        """
        self.scope = scope
        self._receive = receive
        self._send = send

    @property
    def headers(self) -> dict[str, str]:
        """Return the headers provided in the connection.

        Returns:
            dict[str, str]: the connection's headers.
        """
        return {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in self.scope.get("headers", [])
        }

    @property
    def url(self) -> dict[str, str]:
        """Return the URL information provided in the connection.

        The URL is split in to its separate components.

        Returns:
            dict[str, str]: the URL information.
        """
        return {
            "scheme": self.scope.get("scheme"),
            "server": self.scope.get("server"),
            "root_path": self.scope.get("root_path"),
            "path": self.scope.get("path"),
            "query_string": self.scope.get("query_string"),
        }

    async def receive(self) -> dict[str, Any]:
        """Receive an incoming request from the client.

        Raises:
            ProtocolMismatch: if the received request's protocol does not match
                the connection's protocol.

        Returns:
            Request: the received request.
        """
        request = await self._receive()
        request_protocol = request["type"].split(".")[0]

        if request_protocol != self.protocol:
            raise ProtocolMismatch(
                f"Received request protocol ({request_protocol}) does not match this connection protocol ({self.protocol})."
            )

        return request

    async def send(self, response: dict[str, Any]) -> None:
        """Send a response to the client.

        Args:
            event (dict[str, Any]): the response to send.

        Raises:
            ProtocolMismatch: if the response's protocol does not match the
                connection's protocol.
        """
        response_protocol = response["type"].split(".")[0]

        if response_protocol != self.protocol:
            raise ProtocolMismatch(
                f"Response protocol ({response_protocol}) does not match this connection protocol ({self.protocol})."
            )

        await self._send(response)

    @abstractmethod
    async def receive_request(self) -> Request:
        """Receive a request from the client.

        Returns:
            Request: the received request.
        """

    @abstractmethod
    async def send_response(self, response: Response) -> None:
        """Send a response to the client.

        Args:
            response (Response): the response to send.
        """


class HttpConnection(Connection):
    """A HTTP connection.

    This Connection class is capable of receiving requests and sending
    responses that use the protocol http.

    Attributes:
        protocol (str): name of the connection protocol, defaults to http.
    """

    protocol: str = "http"

    @property
    def method(self) -> str:
        """Return the method provided in the connection.

        Returns:
            str: the connection's method.
        """
        return self.scope["method"]

    async def get_requests_body(self) -> Request:
        """Return the requests' body.

        The body is constructed from all the requests received from the client.

        Returns:
            bytes: the constructed body.
        """
        body = b""

        async for request in self.stream_requests():
            body += request.data["body"]

        return Request(
            protocol=self.protocol,
            type="request",
            data={"body": body, "more_body": False},
        )

    async def receive_request(self) -> Request:
        """Receive a request from the client.

        Raises:
            TypeMismatch: the request's type does not equal request.

        Returns:
            Request: the received request.
        """
        request = await self.receive()
        protocol, type = request["type"].split(".")

        if type != "request":
            raise TypeMismatch(
                f"Request type ({type}) does not match the expected type (request)."
            )

        del request["type"]

        return Request(protocol=protocol, type=type, data=request)

    async def send_response(self, response: Response) -> None:
        """Send a response to the client.

        Args:
            response (Response): the response to send.
        """
        for message in response.render_messages():
            await self.send(message)

    async def stream_requests(self) -> Generator[Request, None, None]:
        """Stream the requests.

        The body of each request is yielded.

        Raises:
            RequestTypeMismatch: if a request's type does not match the type receive.

        Yields:
            Generator[bytes, None, None]: the body of a request.
        """
        while True:
            request = await self.receive_request()

            yield request

            if not request.data["more_body"]:
                break


class WebSocketConnection(Connection):
    """A WebSocket connection.

    This connection class is capable of receiving requests and sending responses that have the type websocket.

    Attributes:
        protocol (str): name of the connection protocol, defaults to websocket.
    """

    protocol: str = "websocket"

    def __init__(self, *args):
        super().__init__(*args)

        self.application_connection_state = "connecting"
        self.client_connection_state = "connecting"

    async def receive_request(self) -> Request:
        """Receive a request from the client.

        Raises:
            InvalidConnectionState: the client's connection state is not
            appropriate for the request being received.

        Returns:
            Request: the received request.
        """
        if self.client_connection_state == "disconnected":
            raise InvalidConnectionState(
                "Cannot receive a request from a disconnected client."
            )

        request = await self.receive()
        protocol, type = request["type"].split(".")

        if self.client_connection_state == "connecting":
            if type != "connect":
                raise InvalidConnectionState(
                    f"Cannot receive a {type} request from a connecting client."
                )

            self.client_connection_state = "connected"

        elif self.client_connection_state == "connected":
            if type not in ["receive", "disconnect"]:
                raise InvalidConnectionState(
                    f"Cannot receive a {type} request from a connected client."
                )

            if type == "disconnect":
                self.client_connection_state = "disconnected"

        del request["type"]

        return Request(protocol=protocol, type=type, data=request)

    async def send_response(self, response: Response) -> None:
        """Send a response to the client.

        Args:
            response (Response): the response to send.

        Raises:
            InvalidConnectionState: if the application's connection state is
            not appropriate for the message being sent.
        """
        if self.application_connection_state == "disconnected":
            raise InvalidConnectionState(
                "Cannot send a response when the application has disconnected."
            )

        for message in response.render_messages():
            message_type = message["type"].split(".")[1]

            if (
                self.application_connection_state == "connecting"
                and message_type not in ["accept", "close"]
            ):
                raise InvalidConnectionState(
                    f"Cannot send a {message_type} response when the application is connecting."
                )

            if (
                self.application_connection_state == "connected"
                and message_type not in ["send", "close"]
            ):
                raise InvalidConnectionState(
                    f"Cannot send a {message_type} response when the application is connected."
                )

            if message_type == "close":
                self.application_connection_state = "disconnected"

            await self.send(message)


protocols = {"http": HttpConnection, "websocket": WebSocketConnection}


def make_connection(scope, receive, send) -> type[Connection]:
    """Return a :class:`Connection` instance for a protocol.

    Args:
        scope (dict): [description]
        receive (Coroutine): [description]
        send (Coroutine): [description]

    Raises:
        Exception: if the scope protocol is not available in protocols.

    Returns:
        type[Connection]: a :class:`Connection` instance for the protocol.
    """
    try:
        return protocols[scope["type"]](scope, receive, send)
    except KeyError:
        raise ProtocolUnknown()