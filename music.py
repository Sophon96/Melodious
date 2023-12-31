import asyncio
import collections
import itertools
import typing

import discord

# this is scuffed
# queue was moved below bc python moment
loop: collections.defaultdict[int, bool] = collections.defaultdict(bool)
to_play: asyncio.Queue[discord.VoiceClient] = asyncio.Queue()
# now_playing was moved below bc python moment


class InvidiousVideo(typing.TypedDict):
    title: str
    videoId: str
    author: str
    authorUrl: str


# even more scuffed
queue: collections.defaultdict[
    int, collections.deque[InvidiousVideo]] = collections.defaultdict(
        collections.deque)
now_playing: collections.defaultdict[int, typing.Union[
    InvidiousVideo, None]] = collections.defaultdict(lambda: None)


class SearchDropdown(discord.ui.Select):

    def __init__(
        self,
        data: typing.List[InvidiousVideo],
    ):

        # Set the options that will be presented inside the dropdown

        options = [
            discord.SelectOption(label=video["title"]) for video in data
        ]

        #self.video = None
        self.video_map = {video["title"]: video for video in data}

        # The placeholder is what will be shown when no option is chosen
        # The min and max values indicate we can only pick one of the three options
        # The options parameter defines the dropdown options. We defined this above
        super().__init__(placeholder="Choose a track...",
                         min_values=1,
                         max_values=1,
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Use the interaction object to send a response message containing
        # the user's favourite colour or choice. The self object refers to the
        # Select object, and the values attribute gets a list of the user's
        # selected options. We only want the first one.
        video = self.video_map[self.values[0]]
        # confirm_button = next(
        #     (item for item in self.view.children if item.label == "Confirm"))
        # confirm_button.disabled = False
        # await interaction.edit_original_response(view=self.view)
        if interaction.guild_id is None:
            raise RuntimeError(
                "Interaction guild id is None when trying to add a song to the queue. Solar flare occured?"
            )
        queue[interaction.guild_id].append(video)
        await interaction.delete_original_response()
        await interaction.followup.send(
            f"Added [{video['title']}](<https://youtube.com/watch?v={video['videoId']}>) to the queue!"
        )


# class SearchDropdownView(discord.ui.View):
#
#     def __init__(
#         self,
#         data: typing.List[InvidiousVideo],
#     ):
#         super().__init__()
#
#         # Adds the dropdown to our view object.
#         self.add_item(SearchDropdown(data))
#
#     @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
#     async def cancel(self, interaction: discord.Interaction,
#                      button: discord.ui.Button):
#         await interaction.response.defer()
#         await interaction.delete_original_response()
#         self.stop()
#
#     @discord.ui.button(label="Confirm",
#                        style=discord.ButtonStyle.success,
#                        disabled=True)
#     async def confirm(self, interaction: discord.Interaction,
#                       button: discord.ui.Button):
#         select = self.children[-1]
#         video = select.video
#         queue[interaction.guild_id].append(video)
#         await interaction.response.send_message(
#             f"Added [{video['title']}](<https://youtube.com/watch?v={video['videoId']}>) to the queue!"
#         )
#         self.stop()


class QueuePagerView(discord.ui.View):

    def __init__(self, disable_next: bool = False):
        super().__init__()
        self.page = 0
        next((item for item in self.children
              if isinstance(item, discord.ui.Button) and item.label == "Next"
              )).disabled = disable_next

    @discord.ui.button(label="Back", disabled=True)
    async def last_page(self, interaction: discord.Interaction,
                        button: discord.ui.Button):
        await interaction.response.defer()
        self.page -= 1

        if interaction.guild_id is None:
            raise RuntimeError(
                "Interaction guild id is None when trying to go back a page. Solar flare occured?"
            )
        guild_queue = queue[interaction.guild_id]

        if len(guild_queue) == 0:
            await interaction.edit_original_response(
                content="Nothing in queue!", view=None)
        else:
            q0 = list(
                itertools.islice(guild_queue, 25 * self.page,
                                 25 * (self.page + 1)))
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
            button.disabled = bool(self.page)
            await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Next")
    async def next_page(self, interaction: discord.Interaction,
                        button: discord.ui.Button):
        await interaction.response.defer()
        self.page += 1

        if interaction.guild_id is None:
            raise RuntimeError(
                "Interaction guild id is None when trying to go forward a page. Solar flare occured?"
            )
        guild_queue = queue[interaction.guild_id]

        if len(guild_queue) == 0:
            await interaction.edit_original_response(
                content="Nothing in queue!", view=None)
        else:
            q0 = list(
                itertools.islice(guild_queue, 25 * self.page,
                                 25 * (self.page + 1)))
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
            button.disabled = 25 * (self.page + 1) > len(guild_queue)
            await interaction.edit_original_response(embed=embed, view=self)
