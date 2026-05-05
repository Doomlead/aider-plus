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
            with patch("aider.integrations.discord.EngineeringDepartment") as eng_cls:
                engineering = MagicMock()
                engineering.process = AsyncMock(return_value=MagicMock(payload="done", status="success", metadata={"files": ["foo.py"], "commits": [], "diffs": []}))
                eng_cls.return_value = engineering

                output = await bot.run_instruction(
                    key=key,
                    repo_path="/tmp/repo",
                    user_id=3,
                    prompt="Fix the bug",
                )

        self.assertEqual(output["summary"], "done")
        self.assertEqual(output["files_changed"], ["foo.py"])
        engineering.process.assert_awaited_once()


    async def test_run_instruction_reloads_memory_on_ping(self):
        bot = DiscordAiderBot(config=DiscordAiderConfig(max_runtime_seconds=30))

        key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=3, repo_path="/tmp/repo")
        fake_coder = MagicMock()

        with patch.object(bot, "get_or_create_session", AsyncMock(return_value=fake_coder)):
            with patch.object(bot, "on_reconnect_or_ping") as ping_hook:
                with patch("aider.integrations.discord.EngineeringDepartment") as eng_cls:
                    engineering = MagicMock()
                    engineering.process = AsyncMock(return_value=MagicMock(payload="done", status="success", metadata={}))
                    eng_cls.return_value = engineering

                    await bot.run_instruction(
                        key=key,
                        repo_path="/tmp/repo",
                        user_id=3,
                        prompt="Fix the bug",
                    )

        ping_hook.assert_called_once_with(key)

    async def test_prototype_generates_prd_and_routes_handoff(self):
        bot = DiscordAiderBot(config=DiscordAiderConfig(max_runtime_seconds=30))

        key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=3, repo_path="/tmp/repo")
        fake_coder = MagicMock()

        with patch.object(bot, "get_or_create_session", AsyncMock(return_value=fake_coder)):
            output = await bot.prototype(
                key=key,
                repo_path="/tmp/repo",
                user_id=3,
                prompt="Build a dashboard",
            )

        self.assertEqual(output["artifact_type"], "prd")
        self.assertEqual(output["handoff_to"], "engineering")
        self.assertIn("# PRD", output["summary"])
        self.assertIn("Build a dashboard", output["summary"])

        self.assertEqual(bot.engineering.inbox.qsize(), 1)
        routed_task = bot.engineering.inbox.get_nowait()
        self.assertEqual(routed_task.origin, "product")
        self.assertEqual(routed_task.target, "engineering")
        self.assertEqual(routed_task.payload, output["summary"])
        self.assertFalse(routed_task.blocking)


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
