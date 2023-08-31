import asyncio
import atexit
import itertools
import logging
import typing

import aiohttp
import discord
from discord.ext import tasks

from . import INVIDIOUS_URL, MY_GUILD
from .music import (InvidiousVideo, QueuePagerView, loop,
                    queue, to_play, SearchDropdown, now_playing)

logging.basicConfig(filename="discord.log",
                    filemode="a",
                    encoding="utf-8",
                    level=logging.DEBUG)

intents = discord.Intents.default()


class MyClient(discord.Client):

    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = discord.app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession

    async def setup_hook(self):
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync()
        await self.tree.sync(guild=MY_GUILD)
        self.session = aiohttp.ClientSession()
        play_needed.start()


client = MyClient()


@client.event
async def on_ready():
    print(f'Logged in as {client.user}')


@client.event
async def on_error(event):
    logging.debug("Error occurred", event)
    logging.exception("Error occurred")


@client.event
async def on_interaction(interaction):
    logging.info(f"Interaction received: {interaction}")


# session = aiohttp.ClientSession()


@client.tree.command()
async def search(interaction: discord.Interaction, query: str):
    """Search for a song"""
    await interaction.response.defer()
    await interaction.followup.send("Searching...")

    async with client.session.get(
            f"{INVIDIOUS_URL}/api/v1/search?q={query}&type=video&fields=title,videoId,author,authorUrl"
    ) as resp:
        data: typing.List[InvidiousVideo] = (await resp.json())[:10]

    await interaction.delete_original_response()

    if len(data) == 0:
        return await interaction.followup.send("No results found.")

    results_response = "\n".join(
        f"- [{video['title']}](<https://youtube.com/watch?v={video['videoId']}>)\n"
        f" - By [{video['author']}](<https://youtube.com{video['authorUrl']}>)"
        for video in data)

    view = discord.ui.View()
    view.add_item(SearchDropdown(data))
    await interaction.followup.send(results_response, ephemeral=True, view=view)


@client.tree.command(name="queue")
async def get_queue(interaction: discord.Interaction):
    guild_queue = queue[interaction.guild_id]

    if len(guild_queue) == 0:
        await interaction.response.send_message("Nothing in queue!")
    else:
        q0 = list(itertools.islice(guild_queue, 0, 10))
        resp = "\n".join(
            f"- [{video['title']}](<https://youtube.com/watch?v={video['videoId']}>)\n"
            f" - By [{video['author']}](<https://youtube.com{video['authorUrl']}>)"
            for video in q0)
        await interaction.response.send_message(
            content=resp,
            view=QueuePagerView(disable_next=len(guild_queue) <= 10))


@client.tree.command()
async def play(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if interaction.user.voice is None:
        await interaction.response.send_message(
            "Join a voice channel to start playing!")
        return

    if vc is None:
        vc = await interaction.user.voice.channel.connect()

    if vc.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message(
            "Already playing in a different voice channel!")
        return

    if vc.is_playing():
        await interaction.response.send_message("Already playing music!")
        return

    if vc.is_paused():
        vc.resume()
        await interaction.response.send_message("Resuming...")
        return

    if len(queue[interaction.guild_id]) == 0:
        await interaction.response.send_message("Nothing in queue!")
        return

    await to_play.put(vc)
    await interaction.followup.send("Playing...")


@tasks.loop(seconds=1.0)
async def play_needed():
    while to_play.qsize() > 0:
        vc = await to_play.get()
        guild_queue = queue[vc.guild.id]

        track0 = guild_queue.popleft()
        if loop[vc.guild.id]:
            guild_queue.append(track0)

        async with client.session.get(
                f"{INVIDIOUS_URL}/api/v1/videos/{track0['videoId']}?fields=adaptiveFormats(bitrate,itag,audioQuality)"
        ) as resp:
            formats = [
                i for i in (await resp.json())["adaptiveFormats"]
                if "audioQuality" in i
            ]

        best_audio = sorted(formats,
                            key=lambda e: int(e["bitrate"]),
                            reverse=True)[0]["itag"]

        def after_play(error: typing.Optional[Exception]):
            if error:
                logging.error("Error while playing", exc_info=error)

            fut = asyncio.run_coroutine_threadsafe(to_play.put(vc), client.loop)
            try:
                fut.result()
            except:
                logging.exception("Error while putting vc into to_play")

        vc.play(discord.FFmpegOpusAudio(f"{INVIDIOUS_URL}/latest_version?id={track0['videoId']}&itag={best_audio}&local=true",
                                        before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'),
                after=after_play)

        now_playing[vc.guild.id] = track0


@client.tree.command()
async def pause(interaction: discord.Interaction):
    if interaction.user.voice is None:
        await interaction.response.send_message("Nothing to pause!")
        return

    vc = interaction.guild.voice_client
    if (vc := interaction.guild.voice_client
    ) is not None and vc.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message("Nothing to pause!")
        return

    if vc is not None and vc.is_playing():
        interaction.guild.voice_client.pause()
        await interaction.response.send_message("Pausing...")
        return

    if vc is not None and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("Already paused!")
        return


@client.tree.command(name="loop")
async def toggle_loop(interaction: discord.Interaction):
    loop[interaction.guild_id] ^= True
    await interaction.response.send_message(
        f"Loop {('off', 'on')[loop[interaction.guild_id]]}!")


@client.tree.command(name="now_playing")
async def now_playing_c(interaction: discord.Interaction):
    np = now_playing[interaction.guild_id]
    if np is None:
        await interaction.response.send_message(f"Nothing playing!")
    else:
        await interaction.response.send_message(
            f"Now playing [{np['title']}](<https://youtube.com/watch?{np['videoId']}>)")


@client.tree.command()
async def skip(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if interaction.user.voice is None:
        await interaction.response.send_message("Can't skip if you aren't listening!")
        return
    
    if vc is None:
        await interaction.response.send_message("Can't skip if, not playing anything!")
        return

    if len(queue[interaction.guild.id]) == 0:
        await interaction.response.send_message("Nothing left in queue!")
        await vc.stop()
        return
    
    vc.stop()
    #await to_play.put(vc)
    await interaction.followup.send("Skipped track!")


@atexit.register
def exit_handler():
    eloop = asyncio.get_event_loop()
    eloop.run_until_complete(client.close())
    eloop.run_until_complete(client.session.close())


client.run(input("token: "))
