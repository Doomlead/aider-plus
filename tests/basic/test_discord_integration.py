import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aider.company.project import Project
from aider.company.schemas import CompanyEvent, CompanyTask, EventMessage
from aider.integrations.discord import (
    DiscordAiderBot,
    DiscordAiderConfig,
    DiscordSessionKey,
    RepositoryPolicy,
    format_approval_required_message,
)


class TestRepositoryPolicy(unittest.TestCase):
    def test_validate_rejects_non_whitelisted_repo(self):
        policy = RepositoryPolicy(allowed_roots={"/tmp/allowed"})
        with self.assertRaises(PermissionError):
            policy.validate("/tmp/not-allowed/repo")


class TestDiscordApprovalFormatting(unittest.TestCase):
    def test_formats_approval_required_message(self):
        event = EventMessage(
            event=CompanyEvent.APPROVAL_REQUIRED,
            task_id="task-1",
            payload={
                "project_name": "fastapi-billing",
                "gate_name": "prd_approval",
                "artifact_preview": "Build a subscription billing dashboard",
            },
        )

        message = format_approval_required_message(event)

        self.assertIn("📋 **Product Department Deliverable Ready**", message)
        self.assertIn("Project: `fastapi-billing`", message)
        self.assertIn("Gate: PRD Approval → Engineering", message)
        self.assertIn("> Build a subscription billing dashboard", message)


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
        self.assertIsInstance(bot.active_project, Project)
        self.assertEqual(bot.active_project.name, "repo")
        self.assertEqual(bot.active_project.phase, "prototyping")
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
        bot.active_project = Project(project_id="project-1", name="repo")
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

    async def test_run_prototype_emits_approval_before_engineering_handoff(self):
        bot = DiscordAiderBot(config=DiscordAiderConfig(max_runtime_seconds=30))

        key = DiscordSessionKey(guild_id=1, channel_id=2, user_id=3, repo_path="/tmp/repo")
        fake_coder = MagicMock()

        with patch.object(bot, "get_or_create_session", AsyncMock(return_value=fake_coder)):
            with patch("aider.integrations.discord.EngineeringDepartment") as eng_cls:
                engineering = MagicMock()
                engineering.name = "engineering"
                engineering.receive = AsyncMock()
                eng_cls.return_value = engineering

                seen_events = []

                async def company_event_callback(event):
                    seen_events.append(event)

                output = await bot.run_prototype(
                    key=key,
                    repo_path="/tmp/fastapi-billing",
                    user_id=3,
                    prompt="Build a dashboard",
                    company_event_callback=company_event_callback,
                )
                await asyncio.sleep(0)

        self.assertEqual(output["artifact_type"], "prd")
        self.assertIn("Build a dashboard", output["summary"])
        self.assertIsInstance(bot.active_project, Project)
        self.assertEqual(bot.orchestrator.active_project, bot.active_project)
        engineering.receive.assert_not_awaited()
        approval_events = [
            event for event in seen_events
            if isinstance(event, EventMessage) and event.event == CompanyEvent.APPROVAL_REQUIRED
        ]
        self.assertEqual(len(approval_events), 1)
        self.assertEqual(approval_events[0].payload["project_name"], "fastapi-billing")
        self.assertEqual(approval_events[0].payload["gate_name"], "prd_approval")

        bot.orchestrator.approve(output["task_id"])
        await asyncio.sleep(0)
        self.assertEqual(bot.active_project.phase, "development")
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
