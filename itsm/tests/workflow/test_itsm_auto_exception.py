# -*- coding: utf-8 -*-
import copy
from types import SimpleNamespace
from unittest import mock

from django.db import OperationalError
from django.test import SimpleTestCase

from itsm.component.constants import FAILED, FINISHED, SYSTEM_OPERATE, TRANSITION_OPERATE
from itsm.pipeline_plugins.components.collections.itsm_auto import AutoStateService
from pipeline.core.data.base import DataObject


MODULE = "itsm.pipeline_plugins.components.collections.itsm_auto"


class ApiNodeScheduleExceptionTest(SimpleTestCase):
    def setUp(self):
        self.node = SimpleNamespace(
            id=7,
            name="API node",
            status="RUNNING",
            RUNNING_STATUS=["RUNNING", "QUEUEING"],
            action_type=SYSTEM_OPERATE,
            processors_type="PERSON",
            processors="operator",
            contexts={"existing_context": "keep"},
            error_message=None,
            query_params={"request_id": "keep"},
            save=mock.Mock(),
        )
        self.ticket = mock.Mock(flow_id=4)
        self.ticket.node_status.select_for_update.return_value.get.return_value = self.node
        self.get_ticket = self.patch("Ticket.objects.get", return_value=self.ticket)
        self.create_log = self.patch("TicketEventLog.objects.create")
        self.patch("transaction.atomic")
        self.close_connections = self.patch("close_old_connections")
        self.patch("cache.get", return_value=None)
        self.patch("logger")
        self.service = AutoStateService(name="API node")
        self.data = DataObject(
            inputs={"state_id": "24"},
            outputs={
                "api_config": {"id": 1, "query_params": {"request_id": "keep"}},
                "variables": [{"key": "existing_output", "ref_path": "value"}],
                "params_existing_output": "keep",
                "poll_time": 2,
                "poll_interval": 0,
            },
        )
        self.parent_data = DataObject(inputs={"ticket_id": 4}, outputs={})

    def patch(self, target, **kwargs):
        patcher = mock.patch("{}.{}".format(MODULE, target), **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_unexpected_exception_records_failure_without_changing_inputs_or_outputs(self):
        error = RuntimeError("API response could not be parsed")
        old_outputs = copy.deepcopy(self.data.get_outputs())
        with mock.patch.object(self.service, "get_rsp_content", side_effect=error), mock.patch.object(
            self.service, "do_exit_plugins"
        ) as exit_plugins:
            with self.assertRaises(RuntimeError) as raised:
                self.service.schedule(self.data, self.parent_data)

        self.assertIs(raised.exception, error)
        self.assertEqual(self.node.status, FAILED)
        self.assertEqual(self.node.action_type, TRANSITION_OPERATE)
        self.assertEqual(self.node.error_message, "网络波动异常，数据更新失败，请手动重试")
        self.assertNotIn(str(error), self.node.error_message)
        self.assertEqual(self.node.contexts["existing_context"], "keep")
        self.assertEqual(self.node.contexts["ex_data"], self.node.error_message)
        self.assertEqual(self.node.query_params, {"request_id": "keep"})
        self.assertEqual(self.data.outputs.get("variables"), old_outputs["variables"])
        self.assertEqual(self.data.outputs.get("params_existing_output"), "keep")
        self.assertEqual(self.data.outputs.get("api_config")["query_params"], {"request_id": "keep"})
        self.assertNotIn("service_status", self.data.get_outputs())
        self.assertFalse(self.service.is_schedule_finished())
        self.create_log.assert_called_once()
        self.assertEqual(self.create_log.call_args.kwargs["detail_message"], self.node.error_message)
        self.assertEqual(self.create_log.call_args.kwargs["status"], self.node.id)
        log = self.create_log.call_args.kwargs
        self.assertEqual(
            log["message"].format(name=self.node.name, detail_message=log["detail_message"]),
            "自动处理单据任务 【API node】执行失败：网络波动异常，数据更新失败，请手动重试",
        )
        self.ticket.notify.assert_not_called()
        self.ticket.do_before_exit_state.assert_not_called()
        exit_plugins.assert_not_called()
        self.close_connections.assert_not_called()

    def test_initial_database_lookup_failure_can_still_record_failure(self):
        error = OperationalError(2003, "MySQL hostname resolution failed")
        self.get_ticket.side_effect = [error, self.ticket]

        with self.assertRaises(OperationalError) as raised:
            self.service.schedule(self.data, self.parent_data)

        self.assertIs(raised.exception, error)
        self.close_connections.assert_called_once_with()
        self.assertEqual(self.node.status, FAILED)
        self.create_log.assert_called_once()
        self.assertNotIn("MySQL", self.create_log.call_args.kwargs["detail_message"])

    def test_recording_failure_preserves_original_exception_for_engine(self):
        error = RuntimeError("original API error")
        with mock.patch.object(self.service, "get_rsp_content", side_effect=error), mock.patch.object(
            self.service, "record_schedule_exception", side_effect=OperationalError("database unavailable")
        ):
            with self.assertRaises(RuntimeError) as raised:
                self.service.schedule(self.data, self.parent_data)

        self.assertIs(raised.exception, error)

    def test_completed_or_already_failed_node_is_not_overwritten(self):
        for status in [FINISHED, FAILED, "TERMINATED"]:
            with self.subTest(status=status):
                self.node.status = status
                self.service.record_schedule_exception(4, "24", RuntimeError("late exception"))
                self.assertEqual(self.node.status, status)
        self.node.save.assert_not_called()
        self.create_log.assert_not_called()

    def test_repeated_exception_does_not_duplicate_failure_log(self):
        with mock.patch.object(self.service, "get_rsp_content", side_effect=RuntimeError("temporary error")):
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    self.service.schedule(self.data, self.parent_data)

        self.assertEqual(self.node.status, FAILED)
        self.node.save.assert_called_once()
        self.create_log.assert_called_once()

    def test_exception_during_polling_fails_without_consuming_remaining_polls(self):
        self.data.set_outputs("poll_time", 3)
        with mock.patch.object(self.service, "get_rsp_content", side_effect=RuntimeError("polling error")):
            with self.assertRaises(RuntimeError):
                self.service.schedule(self.data, self.parent_data)

        self.assertEqual(self.node.status, FAILED)
        self.assertEqual(self.data.outputs.get("poll_time"), 3)
        self.create_log.assert_called_once()

    def test_existing_success_failure_and_polling_paths_are_unchanged(self):
        for success, poll_time, expected_result, expected_status in [
            (True, 1, True, FINISHED),
            (False, 1, False, FAILED),
            (False, 2, True, None),
        ]:
            with self.subTest(success=success, poll_time=poll_time):
                self.data.set_outputs("poll_time", poll_time)
                response = {"message": "API result", "data": {"value": "result"}}
                with mock.patch.object(
                    self.service, "get_rsp_content", return_value=(success, response)
                ), mock.patch.object(self.service, "do_exit_plugins") as exit_plugins, mock.patch.object(
                    self.service, "record_schedule_exception"
                ) as record_exception:
                    result = self.service.schedule(self.data, self.parent_data)
                self.assertEqual(result, expected_result)
                record_exception.assert_not_called()
                if expected_status:
                    self.assertEqual(exit_plugins.call_args.kwargs["state_status"], expected_status)
                else:
                    exit_plugins.assert_not_called()
                    self.assertEqual(self.data.outputs.get("poll_time"), 1)

    def test_recording_exception_does_not_prevent_subsequent_successful_schedule(self):
        with mock.patch.object(self.service, "get_rsp_content", side_effect=RuntimeError("temporary error")):
            with self.assertRaises(RuntimeError):
                self.service.schedule(self.data, self.parent_data)

        self.node.status = "RUNNING"
        with mock.patch.object(
            self.service, "get_rsp_content", return_value=(True, {"message": "success", "data": {}})
        ), mock.patch.object(self.service, "do_exit_plugins") as exit_plugins:
            self.assertTrue(self.service.schedule(self.data, self.parent_data))
        self.assertEqual(exit_plugins.call_args.kwargs["state_status"], FINISHED)
