"""Simple web client for interacting with pyatv.

based on https://raw.githubusercontent.com/postlund/pyatv/master/examples/tutorial.py / 
https://pyatv.dev/documentation/tutorial/
"""
import logging
import asyncio
import inspect

from aiohttp import WSMsgType, web

import pyatv

routes = web.RouteTableDef()

class DeviceListener(pyatv.interface.DeviceListener, pyatv.interface.PushListener):
    """Listener for device and push updates events."""

    def __init__(self, app, identifier):
        """Initialize a new DeviceListener."""
        self.app = app
        self.identifier = identifier

    def connection_lost(self, exception: Exception) -> None:
        """Call when connection was lost."""
        self._remove()

    def connection_closed(self) -> None:
        """Call when connection was closed."""
        self._remove()

    def _remove(self):
        self.app["atv"].pop(self.identifier)
        self.app["listeners"].remove(self)

    def playstatus_update(self, updater, playstatus: pyatv.interface.Playing) -> None:
        pass
        # """Call when play status was updated."""
        # clients = self.app["clients"].get(self.identifier, [])
        # for client in clients:
        #     asyncio.ensure_future(client.send_str(str(playstatus)))

    def playstatus_error(self, updater, exception: Exception) -> None:
        pass
        # """Call when an error occurred."""


def web_command(method):
    """Decorate a web request handler."""

    async def _handler(request):
        device_id = request.match_info["id"]
        atv = request.app["atv"].get(device_id)
        if not atv:
            try:
                if "auto_connect" in request.headers:
                    atv = await _connect(request)
                else:
                    return web.Response(text=f"Not connected to {device_id}, enabled auto_connect by setting auto_connect header", status=500)
            except Exception as ex:
                return web.Response(text=f"Not connected to {device_id}, tried to reconnect but failed: {ex}", status=500)
        return await method(request, atv)

    return _handler


def add_credentials(config, headers):
    """Add credentials to pyatv device configuration."""
    for service in config.services:
        proto_name = service.protocol.name.lower()
        key = f"{proto_name}_credentials"
        if key not in headers:
            continue
        config.set_credentials(service.protocol, headers[key].strip())


# @routes.get("/status/{id}")
# async def state(request):
#     """Handle request to receive push updates."""
#     return web.Response(
#         text=PAGE.replace("DEVICE_ID", request.match_info["id"]),
#         content_type="text/html",
#     )


@routes.get("/scan")
async def scan(request):
    """Handle request to scan for devices."""
    results = await pyatv.scan(loop=asyncio.get_event_loop())
    output = "\n\n".join(str(result) for result in results)
    return web.Response(text=output)


@routes.get("/connect/{id}")
async def connect(request):
    """Handle request to connect to a device."""
    try:
        atv = await _connect(request)
    except Exception as ex:
        return web.Response(text=f"failed to connect: {ex}")
    return web.Response(text="OK")


async def _connect(request):
    loop = asyncio.get_event_loop()
    device_id = request.match_info["id"]
    if device_id in request.app["atv"]:
        print("already connected to:", device_id)
        return request.app["atv"][device_id]

    results = await pyatv.scan(identifier=device_id, loop=loop)
    if not results:
        raise Exception("Device not found")
    

    add_credentials(results[0], request.headers)

    try:
        atv = await pyatv.connect(results[0], loop=loop)
    except Exception as ex:
        raise Exception(f"Failed to connect to device (validate credentials are correct): {ex}")

    listener = DeviceListener(request.app, device_id)
    atv.listener = listener
    # atv.push_updater.listener = listener
    # atv.push_updater.start()
    request.app["listeners"].append(listener)

    request.app["atv"][device_id] = atv
    return atv


@routes.get("/remote_control/{id}/{command}")
@web_command
async def remote_control(request, atv):
    """Handle remote control command request."""
    try:
        await getattr(atv.remote_control, request.match_info["command"])()
    except Exception as ex:
        return web.Response(text=f"Remote control command failed: {ex}")
    return web.Response(text="OK")

@routes.get("/apps/{id}/list")
@web_command
async def list_apps(request, atv):
    """Handle remote control command request."""
    try:
        apps = await atv.apps.app_list()
    except Exception as ex:
        return web.Response(text=f"Listing app command failed: {ex}")
    return web.Response(text=str(apps))

@routes.get("/apps/{id}/open/{app_identifier}")
@web_command
async def open_app(request, atv):
    """Handle remote control command request."""
    try:
        await atv.apps.launch_app(request.match_info["app_identifier"])
    except Exception as ex:
        return web.Response(text=f"Open app command failed: {ex}")
    return web.Response(text="OK")


@routes.get("/playing/{id}")
@web_command
async def playing(request, atv):
    """Handle request for current play status."""
    try:
        status = await atv.metadata.playing()
    except Exception as ex:
        return web.Response(text=f"Remote control command failed: {ex}")
    return web.Response(text=str(status))


@routes.get("/command/{id}/{command}")
@web_command
async def run_command(request, atv):
    """Handle generic command request. Similar to how atvremote cli works"""
    command = request.match_info["command"]

    try:
        response = await _run_command(atv, command, request.query)
        return web.Response(text=response)
    except NotImplementedError:
        return web.Response(text=f"Command '{command}' is not supported by device")
    except pyatv.exceptions.AuthenticationError as ex:
        return web.Response(text=f"Authentication error: {str(ex)}")
    except Exception as ex:
        return web.Response(text=f"failed to run command: {ex}")
    return web.Response(text="OK")

async def _run_command(atv, cmd, query):
    # based on: https://github.com/postlund/pyatv/blob/master/pyatv/scripts/atvremote.py
    ctrl = pyatv.interface.retrieve_commands(pyatv.interface.RemoteControl)
    metadata = pyatv.interface.retrieve_commands(pyatv.interface.Metadata)
    power = pyatv.interface.retrieve_commands(pyatv.interface.Power)
    playing = pyatv.interface.retrieve_commands(pyatv.interface.Playing)
    stream = pyatv.interface.retrieve_commands(pyatv.interface.Stream)
    device_info = pyatv.interface.retrieve_commands(pyatv.interface.DeviceInfo)
    audio = pyatv.interface.retrieve_commands(pyatv.interface.Audio)
    apps = pyatv.interface.retrieve_commands(pyatv.interface.Apps)

    if cmd in audio:
        return await _exec_command(atv.audio, cmd, *query)

    if cmd in ctrl:
        return await _exec_command(atv.remote_control, cmd, *query)

    if cmd in metadata:
        return await _exec_command(atv.metadata, cmd, *query)

    if cmd in power:
        return await _exec_command(atv.power, cmd, *query)

    if cmd in playing:
        playing_resp = await atv.metadata.playing()
        return await _exec_command(playing_resp, cmd, *query)

    if cmd in stream:
        return await _exec_command(atv.stream, cmd, *query)

    if cmd in device_info:
        return await _exec_command(atv.device_info, cmd, *query)

    if cmd in apps:
        return await _exec_command(atv.apps, cmd, *query)

    raise Exception(f"Unknown command: {cmd}")

async def _exec_command(obj, command, *args):
    # If the command to execute is a @property, the value returned by that
    # property will be stored in tmp. Otherwise it's a coroutine and we
    # have to yield for the result and wait until it is available.
    tmp = getattr(obj, command)
    if inspect.ismethod(tmp):
        value = await tmp(*args)
    else:
        value = tmp

    return value


@routes.get("/command/list")
async def open_app(request):
    """Handle generic command request. Similar to how atvremote cli works"""
    # based on: https://github.com/postlund/pyatv/blob/master/pyatv/scripts/atvremote.py
    commands = []
    commands.append(_stringify_commands("Remote control", pyatv.interface.RemoteControl))
    commands.append(_stringify_commands("Metadata", pyatv.interface.Metadata))
    commands.append(_stringify_commands("Power", pyatv.interface.Power))
    commands.append(_stringify_commands("Playing", pyatv.interface.Playing))
    commands.append(_stringify_commands("AirPlay", pyatv.interface.Stream))
    commands.append(_stringify_commands("Device Info", pyatv.interface.DeviceInfo))
    commands.append(_stringify_commands("Apps", pyatv.interface.Apps))

    return web.Response(text="\n".join(commands))

def _stringify_commands(title, api):
    cmd_list = pyatv.interface.retrieve_commands(api)
    return " - " + "\n - ".join(
        map(lambda x: x[0] + " - " + x[1], sorted(cmd_list.items()))
    )


@routes.get("/close/{id}")
@web_command
async def close_connection(request, atv):
    """Handle request to close a connection."""
    atv.close()
    return web.Response(text="OK")

async def on_shutdown(app: web.Application) -> None:
    """Call when application is shutting down."""
    for atv in app["atv"].values():
        atv.close()



def main():
    """Script starts here."""
    app = web.Application()
    app["atv"] = {}
    app["listeners"] = []
    app.add_routes(routes)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app)


if __name__ == "__main__":
    main()


# import asyncio
# from aiohttp import web
# import pyatv
# from pyatv.conf import AppleTV, ManualService
# from ipaddress import IPv4Address
# from pyatv.const import (
#     Protocol
# )
# import os

# routes = web.RouteTableDef()

# @routes.get('/')
# async def scan(request):
#     return web.Response(text="Hello world!")

# @routes.get('/test')
# async def scan(request):
#     atv = request.app["atv"]
#     if atv == None:
#         print("is none")
#         config = AppleTV(IPv4Address("192.168.50.63"), "")
#         service = ManualService(os.getenv("AIRPLAY_ATV_IDENTIFIER"), Protocol.Companion, 49152, {})
#         service.credentials = os.getenv("AIRPLAY_ATV_CREDENTIALS")
#         config.add_service(service)
#         service_airplay = ManualService(os.getenv("AIRPLAY_ATV_IDENTIFIER"), Protocol.AirPlay, 7000, {})
#         config.add_service(service_airplay)

#         loop = asyncio.get_event_loop()
#         atv = await pyatv.connect(config, loop, protocol=Protocol.Companion)
#         request.app["atv"] = atv


#     print("play_pausing")
#     await atv.remote_control.play_pause()
#     return web.Response(text="Hello world!")


# def main():
#     app = web.Application()
#     app["atv"] = {}
#     app.add_routes(routes)
#     web.run_app(app)

# if __name__ == "__main__":
#     main()