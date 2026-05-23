import time

from nome_voice.actions import ActionRunner


def test_stop_all_cancels_existing_action_generation_quickly() -> None:
    runner = ActionRunner()
    generation = runner._cancel_generation

    runner.stop_all()
    started_at = time.monotonic()

    assert runner._sleep(1.0, generation) is False
    assert time.monotonic() - started_at < 0.2


def test_parallel_mode_runs_each_action_without_daemon() -> None:
    runner = ActionRunner()
    seen: list[str] = []

    runner._ensure_device = lambda: None
    runner._execute_action = lambda action, _generation=None: seen.append(action["type"])

    runner.execute(
        [{"type": "delay", "ms": 1}, {"type": "type_text", "text": "x"}],
        mode="parallel",
    )

    assert sorted(seen) == ["delay", "type_text"]


def test_nome_voice_special_action_uses_handler_without_input_device() -> None:
    seen: list[str] = []
    runner = ActionRunner(on_special_action=lambda action: seen.append(action["type"]))

    runner.execute([{"type": "nome_stop_listening"}])

    assert seen == ["nome_stop_listening"]


def test_emergency_stop_special_action_cancels_current_generation() -> None:
    runner = ActionRunner(on_special_action=lambda _action: None)
    generation = runner._cancel_generation

    runner.execute([{"type": "nome_emergency_stop"}], generation=generation)

    assert runner._cancelled(generation) is True


def test_blank_system_command_does_not_need_input_device() -> None:
    runner = ActionRunner()

    runner.execute([{"type": "system_command", "command": ""}])
