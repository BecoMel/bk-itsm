# -*- coding: utf-8 -*-
"""节点提交状态及 OpenAPI 异常响应回归，不访问数据库和流程引擎。"""

import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from itsm.component.drf.exception import ValidationError as CustomValidationError
from itsm.openapi.decorators import catch_openapi_exception
from itsm.openapi.ticket.validators import openapi_operate_validate
from itsm.openapi.ticket.views import TicketViewSet


class OpenApiExceptionTest(SimpleTestCase):
    def test_native_and_custom_validation_errors_keep_their_message(self):
        for error_type in (ValidationError, CustomValidationError):
            with self.subTest(error_type=error_type):

                @catch_openapi_exception
                def endpoint(view, request):
                    raise error_type("单据已关闭，无法操作")

                with patch("itsm.openapi.decorators.logger") as logger:
                    response = endpoint(None, None)

                self.assertFalse(response.data["result"])
                self.assertEqual(response.data["code"], 3900005)
                self.assertIn("单据已关闭，无法操作", response.data["message"])
                logger.exception.assert_not_called()

    def test_field_validation_error_keeps_field_name(self):
        @catch_openapi_exception
        def endpoint(view, request):
            raise ValidationError({"operator": ["该字段是必填项。"]})

        response = endpoint(None, None)
        self.assertEqual(response.data["code"], 3900005)
        self.assertIn("operator", response.data["message"])

    def test_unexpected_error_is_logged_without_exposing_details(self):
        @catch_openapi_exception
        def endpoint(view, request):
            raise RuntimeError("internal database details")

        with patch("itsm.openapi.decorators.logger") as logger:
            response = endpoint(None, None)

        self.assertEqual(response.data["code"], 3900004)
        self.assertEqual(response.data["message"], "接口异常，请检查请求参数后重试")
        logger.exception.assert_called_once_with("openapi unexpected error")

    def test_success_response_is_unchanged(self):
        response = Response({"ok": True})

        @catch_openapi_exception
        def endpoint(view, request):
            return response

        self.assertIs(endpoint(None, None), response)


class OperateNodeValidationTest(SimpleTestCase):
    def setUp(self):
        self.status = Mock(
            id=123,
            status="RUNNING",
            type="APPROVAL",
            processors_type="PERSON",
        )
        self.status.can_operate.return_value = True
        self.status.is_operator.return_value = True
        self.ticket = Mock(is_over=False, is_slave=False)
        self.ticket.status.return_value = self.status
        self.ticket.activity_callback.return_value = SimpleNamespace(result=True)
        self.request = SimpleNamespace(
            data={
                "sn": "REQ_TEST",
                "state_id": 3642,
                "action_type": "TRANSITION",
                "operator": "alice",
                "fields": [],
            }
        )
        ticket_patch = patch(
            "itsm.openapi.ticket.views.Ticket.objects.get", return_value=self.ticket
        )
        self.addCleanup(ticket_patch.stop)
        ticket_patch.start()
        task_patch = patch("itsm.openapi.ticket.validators.SignTask.objects.filter")
        self.addCleanup(task_patch.stop)
        self.tasks = task_patch.start()
        self.tasks.return_value.first.return_value = None
        serializer_patch = patch("itsm.openapi.ticket.views.TicketProceedSerializer")
        self.addCleanup(serializer_patch.stop)
        self.serializer = serializer_patch.start()
        self.serializer.return_value.validated_data = {"operator": "alice", "fields": []}
        # 单测直接调用业务方法；网关认证由已有中间件负责。
        self.operate_node = catch_openapi_exception(
            inspect.unwrap(TicketViewSet.operate_node)
        )

    def assert_rejected(self, message):
        response = self.operate_node(TicketViewSet(), self.request)
        self.assertFalse(response.data["result"])
        self.assertEqual(response.data["code"], 3900005)
        self.assertIn(message, response.data["message"])
        self.ticket.activity_callback.assert_not_called()
        self.ticket.node_status.filter.assert_not_called()
        self.serializer.assert_not_called()

    def test_closed_ticket_is_rejected_before_submission(self):
        self.ticket.is_over = True
        self.assert_rejected("单据已关闭")
        self.ticket.status.assert_not_called()

    def test_slave_ticket_remains_read_only(self):
        self.ticket.is_slave = True
        self.assert_rejected("子单为只读状态")

    def test_missing_node_has_specific_error(self):
        self.ticket.status.return_value = None
        self.assert_rejected("没有找到该节点")

    def test_finished_node_has_specific_error(self):
        self.status.status = "FINISHED"
        self.assert_rejected("已处理完成，请刷新")
        self.tasks.assert_not_called()

    def test_current_approver_in_progress_cannot_submit_again(self):
        for node_type in ("SIGN", "APPROVAL"):
            for node_status in ("RUNNING", "QUEUEING"):
                with self.subTest(node_type=node_type, node_status=node_status):
                    self.status.type = node_type
                    self.status.status = node_status
                    self.tasks.return_value.first.return_value = Mock(status="RUNNING")
                    self.assert_rejected("正在处理中，请勿重复提交")
                    self.tasks.assert_called_with(
                        status_id=123,
                        processor="alice",
                        status__in=["RUNNING", "EXECUTED", "FINISHED"],
                    )

    def test_current_approver_already_processed_has_specific_error(self):
        for node_type in ("SIGN", "APPROVAL"):
            for node_status in ("RUNNING", "QUEUEING"):
                for task_status in ("EXECUTED", "FINISHED"):
                    with self.subTest(
                        node_type=node_type,
                        node_status=node_status,
                        task_status=task_status,
                    ):
                        self.status.type = node_type
                        self.status.status = node_status
                        self.tasks.return_value.first.return_value = Mock(status=task_status)
                        self.assert_rejected("您已处理该节点")

    def test_other_approvers_can_still_submit(self):
        for node_type in ("SIGN", "APPROVAL"):
            for node_status in ("RUNNING", "QUEUEING"):
                with self.subTest(node_type=node_type, node_status=node_status):
                    self.status.type = node_type
                    self.status.status = node_status
                    self.ticket.reset_mock()
                    response = self.operate_node(TicketViewSet(), self.request)
                    self.assertIsNone(response.data)
                    self.tasks.assert_called_with(
                        status_id=123,
                        processor="alice",
                        status__in=["RUNNING", "EXECUTED", "FINISHED"],
                    )
                    self.ticket.activity_callback.assert_called_once_with(
                        3642, "alice", [], "API"
                    )

    def test_normal_node_first_submission_still_calls_pipeline(self):
        self.status.type = "NORMAL"
        response = self.operate_node(TicketViewSet(), self.request)
        self.assertIsNone(response.data)
        self.ticket.node_status.filter.return_value.update.assert_called_once_with(
            status="QUEUEING"
        )
        self.ticket.activity_callback.assert_called_once_with(3642, "alice", [], "API")
        self.tasks.assert_not_called()

    def test_normal_node_in_progress_rejects_duplicate_submission(self):
        self.status.type = "NORMAL"
        self.status.status = "QUEUEING"
        self.assert_rejected("正在处理中，请勿重复提交")

    def test_open_node_in_progress_rejects_duplicate_submission(self):
        self.status.type = "NORMAL"
        self.status.status = "QUEUEING"
        self.status.processors_type = "OPEN"
        self.status.is_operator.return_value = False
        self.assert_rejected("正在处理中，请勿重复提交")

    def test_unauthorized_user_is_still_rejected(self):
        self.status.can_operate.return_value = False
        self.assert_rejected("不能操作该节点")

    def test_unauthorized_normal_node_user_does_not_get_processing_message(self):
        self.status.type = "NORMAL"
        self.status.status = "QUEUEING"
        self.status.is_operator.return_value = False
        self.status.can_operate.return_value = False
        self.assert_rejected("不能操作该节点")

    def test_invalid_fields_are_rejected_before_changing_node_status(self):
        self.serializer.return_value.is_valid.side_effect = ValidationError(
            {"fields": ["字段值不合法"]}
        )
        response = self.operate_node(TicketViewSet(), self.request)
        self.assertFalse(response.data["result"])
        self.assertEqual(response.data["code"], 3900005)
        self.assertIn("fields", response.data["message"])
        self.ticket.activity_callback.assert_not_called()
        self.ticket.node_status.filter.assert_not_called()

    def test_other_actions_keep_existing_permission_checks(self):
        self.status.status = "QUEUEING"
        openapi_operate_validate("alice", self.ticket, 3642, "DELIVER")
        self.tasks.assert_not_called()
        self.status.can_operate.assert_called_once_with("alice", "DELIVER")

    def test_callback_failure_is_not_reported_as_success(self):
        self.ticket.activity_callback.return_value = SimpleNamespace(
            result=False, message="callback failed"
        )
        response = self.operate_node(TicketViewSet(), self.request)
        self.assertFalse(response.data["result"])
        self.assertEqual(response.data["code"], 3900004)
        self.assertIn("callback failed", response.data["message"])
        self.ticket.node_status.filter.return_value.update.assert_called_with(
            status="RUNNING"
        )
