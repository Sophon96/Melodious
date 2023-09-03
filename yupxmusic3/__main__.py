import asyncio
import atexit
import itertools
import logging
import typing

import aiohttp
import discord
from discord.ext import tasks

from . import INVIDIOUS_URL, MY_GUILD
from .music import (InvidiousVideo, QueuePagerView, loop, queue, to_play,
                    SearchDropdown, now_playing)

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
        data: typing.List[InvidiousVideo] = (await resp.json())[:25]

    await interaction.delete_original_response()

    if len(data) == 0:
        return await interaction.followup.send("No results found.")

    embed = discord.Embed(colour=discord.Colour.orange(),
                          title="Search results",
                          timestamp=interaction.created_at)
    # map(
    #     lambda e: embed.add_field(
    #         name=e["title"],
    #         value=f"By [{e['author']}](https://youtube.com{e['authorUrl']})\n"
    #               f"[View on YouTube](https://youtu.be/{e['videoId']})",
    #         inline=False), data)
    for track in data:
        embed.add_field(
            name=track["title"],
            value=
            f"By [{track['author']}](https://youtube.com{track['authorUrl']})\n"
            f"[View on YouTube](https://youtu.be/{track['videoId']})",
            inline=False)

    # results_response = "\n".join(
    #     f"- [{video['title']}](<https://youtu.be/{video['videoId']}>)\n"
    #     f" - By [{video['author']}](<https://youtube.com{video['authorUrl']}>)"
    #     for video in data)

    view = discord.ui.View()
    view.add_item(SearchDropdown(data))
    await interaction.followup.send(embed=embed, ephemeral=True, view=view)


@client.tree.command(name="queue")
async def get_queue(interaction: discord.Interaction):
    await interaction.response.defer()

    if interaction.guild is None or interaction.guild_id is None or isinstance(
            interaction.user, discord.User):
        await interaction.followup.send(
            "This command can only be used in a server!")
        return

    guild_queue = queue[interaction.guild_id]

    if len(guild_queue) == 0:
        await interaction.followup.send("Nothing in queue!")
    else:
        q0 = list(itertools.islice(guild_queue, 0, 25))
        embed = discord.Embed(colour=discord.Colour.orange(),
                              title="Queued Tracks",
                              timestamp=interaction.created_at)
        for track in q0:
            embed.add_field(
                name=track["title"],
                value=
                f"By [{track['author']}](https://youtube.com{track['authorUrl']})\n"
                f"[View on YouTube](https://youtu.be/{track['videoId']})",
                inline=False)
        # resp = "\n".join(
        #     f"- [{video['title']}](<https://youtube.com/watch?v={video['videoId']}>)\n"
        #     f" - By [{video['author']}](<https://youtube.com{video['authorUrl']}>)"
        #     for video in q0)
        await interaction.followup.send(
            embed=embed,
            # content=resp,
            view=QueuePagerView(disable_next=len(guild_queue) <= 25))


@client.tree.command()
async def play(interaction: discord.Interaction):
    await interaction.response.defer()

    if interaction.guild is None or interaction.guild_id is None or isinstance(
            interaction.user, discord.User):
        await interaction.followup.send(
            "This command can only be used in a server!")
        return

    vc = interaction.guild.voice_client
    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.followup.send(
            "Join a voice channel to start playing!")
        return

    if vc is None:
        vc = await interaction.user.voice.channel.connect()

    if not isinstance(vc, discord.VoiceClient):
        await interaction.followup.send(
            "Something unexpected happened: somehow didn't join a voice channel. Please try again and report this to the developer!"
        )
        raise RuntimeError(
            "Somehow did not join a voice channel when asked to play")

    if vc.channel.id != interaction.user.voice.channel.id:
        await interaction.followup.send(
            "Already playing in a different voice channel!")
        return

    if vc.is_playing():
        await interaction.followup.send("Already playing music!")
        return

    if vc.is_paused():
        vc.resume()
        await interaction.followup.send("Resuming...")
        return

    if len(queue[interaction.guild_id]) == 0:
        await interaction.followup.send("Nothing in queue!")
        return

    await to_play.put(vc)
    await interaction.followup.send("Playing...")


@tasks.loop(seconds=1.0)
async def play_needed():
    while to_play.qsize() > 0:
        vc = await to_play.get()
        guild_queue = queue[vc.guild.id]

        if len(guild_queue) == 0:
            now_playing[vc.guild.id] = None
            continue

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

            fut = asyncio.run_coroutine_threadsafe(to_play.put(vc),
                                                   client.loop)
            try:
                fut.result()
            except:
                logging.exception("Error while putting vc into to_play")

        vc.play(discord.FFmpegOpusAudio(
            f"{INVIDIOUS_URL}/latest_version?id={track0['videoId']}&itag={best_audio}&local=true",
            before_options=
            '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'),
                after=after_play)

        now_playing[vc.guild.id] = track0


@client.tree.command()
async def pause(interaction: discord.Interaction):
    await interaction.response.defer()

    if interaction.guild is None or isinstance(interaction.user, discord.User):
        await interaction.followup.send(
            "This command can only be used in a server!")
        return

    vc = interaction.guild.voice_client
    if interaction.user.voice is None or interaction.user.voice.channel is None or vc is None:
        await interaction.followup.send("Nothing to pause!")
        return

    if not isinstance(vc, discord.VoiceClient):
        await interaction.followup.send(
            "Something unexpected happened: received unknown subclass of VoiceProtocol (not VoiceClient). Please try again and report this to the developer!"
        )
        raise RuntimeError(
            "Got unknown subclass of VoiceProtocl (not VoiceClient)")

    if vc.channel.id != interaction.user.voice.channel.id:
        await interaction.followup.send("Nothing to pause!")
        return

    if vc.is_playing():
        vc.pause()
        await interaction.followup.send("Pausing...")
        return

    if vc.is_paused():
        await interaction.followup.send("Already paused!")
        return


@client.tree.command()
async def resume(interaction: discord.Interaction):
    if interaction.guild is None or isinstance(interaction.user, discord.User):
        await interaction.followup.send(
            "This command can only be used in a server!")
        return

    vc = interaction.guild.voice_client
    await interaction.response.defer()

    if interaction.user.voice is None or interaction.user.voice.channel is None or vc is None:
        await interaction.followup.send("Nothing to resume!")
        return

    if not isinstance(vc, discord.VoiceClient):
        await interaction.followup.send(
            "Something unexpected happened: received unknown subclass of VoiceProtocol (not VoiceClient). Please try again and report this to the developer!"
        )
        raise RuntimeError(
            "Got unknown subclass of VoiceProtocl (not VoiceClient)")

    if vc.channel.id != interaction.user.voice.channel.id:
        await interaction.followup.send("Nothing to resume!")
        return

    if vc.is_paused():
        vc.pause()
        await interaction.followup.send("Resuming...")
        return

    if vc.is_playing():
        await interaction.followup.send("Already playing!")
        return


@client.tree.command(name="loop")
async def toggle_loop(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message(
            "This command can only be used in a server!")
        return

    loop[interaction.guild_id] ^= True
    await interaction.response.send_message(
        f"Loop {('off', 'on')[loop[interaction.guild_id]]}!")


@client.tree.command(name="now_playing")
async def now_playing_c(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message(
            "This command can only be used in a server!")
        return

    np = now_playing[interaction.guild_id]
    if np is None:
        await interaction.response.send_message(f"Nothing playing!")
    else:
        await interaction.response.send_message(
            f"Now playing [{np['title']}](<https://youtube.com/watch?{np['videoId']}>)"
        )


@client.tree.command()
async def skip(interaction: discord.Interaction):
    await interaction.response.defer()

    if interaction.guild is None or isinstance(interaction.user, discord.User):
        await interaction.followup.send(
            "This command can only be used in a server!")
        return

    vc = interaction.guild.voice_client

    if not isinstance(vc, discord.VoiceClient):
        await interaction.followup.send(
            "Something unexpected happened: received unknown subclass of VoiceProtocol (not VoiceClient). Please try again and report this to the developer!"
        )
        raise RuntimeError(
            "Got unknown subclass of VoiceProtocl (not VoiceClient)")

    if interaction.user.voice is None:
        await interaction.followup.send("Can't skip if you aren't listening!")
        return

    if vc is None:
        await interaction.followup.send("Can't skip if not playing anything!")
        return

    if len(queue[interaction.guild.id]) == 0:
        await interaction.followup.send("Nothing left in queue!")
        vc.stop()
        return

    vc.stop()
    #await to_play.put(vc)
    await interaction.followup.send("Skipped track!")


@client.tree.command()
async def clear_queue(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message(
            "This command can only be used in a server!")
        return

    queue[interaction.guild_id].clear()
    await interaction.response.send_message("Cleared queue!")


@atexit.register
def exit_handler():
    eloop = asyncio.get_event_loop()
    eloop.run_until_complete(client.close())
    eloop.run_until_complete(client.session.close())


client.run(input("token: "))
