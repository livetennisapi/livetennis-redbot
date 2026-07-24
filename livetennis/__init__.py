from redbot.core.bot import Red

from .livetennis import LiveTennis


async def setup(bot: Red) -> None:
    await bot.add_cog(LiveTennis(bot))
