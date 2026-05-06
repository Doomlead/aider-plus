import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aider.company.schemas import CompanyTask
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
                engineering.process = AsyncMock(
                    return_value=MagicMock(
                        payload="done",
                        status="success",
                        metadata={"files": ["foo.py"], "commits": [], "diffs": []},
                    )
                )
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
                    engineering.process = AsyncMock(
                        return_value=MagicMock(payload="done", status="success", metadata={})
                    )
                    eng_cls.return_value = engineering

                    await bot.run_instruction(
                        key=key,
                        repo_path="/tmp/repo",
                        user_id=3,
                        prompt="Fix the bug",
                    )

        ping_hook.assert_called_once_with(key)

    async def test_receive_human_input_bootstraps_without_active_project(self):
        bot = DiscordAiderBot(config=DiscordAiderConfig(max_runtime_seconds=30))
        key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=3, repo_path="/tmp/repo")

        with patch.object(
            bot, "run_prototype", AsyncMock(return_value={"artifact_type": "prd"})
        ) as run_prototype:
            output = await bot.receive_human_input(
                key=key,
                repo_path="/tmp/repo",
                user_id=3,
                prompt="Build a dashboard",
            )

        self.assertEqual(output["artifact_type"], "prd")
        run_prototype.assert_awaited_once_with(
            key=key,
            repo_path="/tmp/repo",
            user_id=3,
            prompt="Build a dashboard",
            model=None,
            callback=None,
        )

    async def test_receive_human_input_routes_active_project_iteration_to_engineering(self):
        bot = DiscordAiderBot(config=DiscordAiderConfig(max_runtime_seconds=30))
        bot.active_project = object()
        key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=3, repo_path="/tmp/repo")

        with patch.object(
            bot, "run_instruction", AsyncMock(return_value={"summary": "done"})
        ) as run_instruction:
            output = await bot.receive_human_input(
                key=key,
                repo_path="/tmp/repo",
                user_id=3,
                prompt="Iterate on the dashboard",
            )

        self.assertEqual(output["summary"], "done")
        run_instruction.assert_awaited_once_with(
            key=key,
            repo_path="/tmp/repo",
            user_id=3,
            prompt="Iterate on the dashboard",
            model=None,
            callback=None,
        )

    async def test_run_prototype_routes_product_handoff_to_engineering(self):
        bot = DiscordAiderBot(config=DiscordAiderConfig(max_runtime_seconds=30))

        key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=3, repo_path="/tmp/repo")
        fake_coder = MagicMock()

        with patch.object(bot, "get_or_create_session", AsyncMock(return_value=fake_coder)):
            with patch("aider.integrations.discord.EngineeringDepartment") as eng_cls:
                engineering = MagicMock()
                engineering.name = "engineering"
                engineering.receive = AsyncMock()
                eng_cls.return_value = engineering

                output = await bot.run_prototype(
                    key=key,
                    repo_path="/tmp/repo",
                    user_id=3,
                    prompt="Build a dashboard",
                )

        self.assertEqual(output["artifact_type"], "prd")
        self.assertIn("Build a dashboard", output["summary"])
        engineering.receive.assert_awaited_once()
        routed_task = engineering.receive.await_args.args[0]
        self.assertIsInstance(routed_task, CompanyTask)
        self.assertEqual(routed_task.origin, "product")
        self.assertEqual(routed_task.target, "engineering")
        self.assertEqual(routed_task.payload["prd_content"], output["summary"])
        self.assertEqual(routed_task.payload["original_request"], "Build a dashboard")
        self.assertIsNotNone(routed_task.payload.get("prd_content"))
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
