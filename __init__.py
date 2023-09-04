import breadcord

from .cog import MelodiousCog


async def setup(bot: breadcord.Bot):
    await bot.add_cog(MelodiousCog())
