import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aider.integrations.discord import (
    DiscordAiderBot,
    DiscordAiderConfig,
    DiscordSessionKey,
    RepositoryPolicy,
)


class TestRepositoryPolicy(unittest.TestCase):
    def test_validate_rejects_non_whitelisted_repo(self):
        policy = RepositoryPolicy(allowed_roots={"/tmp/allowed"})
        with self.assertRaises(PermissionError):
            policy.validate("/tmp/not-allowed/repo")


class TestDiscordAiderBot(unittest.IsolatedAsyncioTestCase):
    async def test_run_instruction_returns_structured_output(self):
        bot = DiscordAiderBot(config=DiscordAiderConfig(max_runtime_seconds=30))

        key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=3, repo_path="/tmp/repo")
        fake_coder = MagicMock()

        with patch.object(bot, "get_or_create_session", AsyncMock(return_value=fake_coder)):
            with patch("aider.integrations.discord.AiderAgentLoop") as loop_cls:
                loop = MagicMock()
                loop.run = AsyncMock(return_value={"summary": "done", "files_changed": ["foo.py"]})
                loop_cls.return_value = loop

                output = await bot.run_instruction(
                    key=key,
                    repo_path="/tmp/repo",
                    user_id=3,
                    prompt="Fix the bug",
                )

        self.assertEqual(output["summary"], "done")
        loop.run.assert_awaited_once()

    async def test_denied_user(self):
        bot = DiscordAiderBot(config=DiscordAiderConfig(deny_users={99}))
        key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=99, repo_path="/tmp/repo")

        with self.assertRaises(PermissionError):
            await bot.run_instruction(
                key=key,
                repo_path="/tmp/repo",
                user_id=99,
                prompt="hello",
            )


if __name__ == "__main__":
    unittest.main()
