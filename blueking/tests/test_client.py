# -*- coding: utf-8 -*-
"""
Tencent is pleased to support the open source community by making BK-ITSM 蓝鲸流程服务 available.

Copyright (C) 2025 Tencent.  All rights reserved.

BK-ITSM 蓝鲸流程服务 is licensed under the MIT License.

License for BK-ITSM 蓝鲸流程服务:
--------------------------------------------------------------------
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

from unittest import mock

from django.test import TestCase

from blueking.component.open.client import ComponentClient


class TestAPI(TestCase):

    def setUp(self):
        self.client = ComponentClient(
            bk_app_code='test',
            bk_app_secret='xxx',
            common_args={
                'bk_username': 'admin',
            }
        )
        self.client.set_bk_api_ver('v2')

    def test_api(self):
        params = {
            'bk_biz_id': 1,
        }
        expected = {"result": True, "data": []}
        response = mock.Mock(status_code=200)
        response.json.return_value = expected
        with mock.patch.object(
            ComponentClient, "request", return_value=response
        ) as request:
            result = self.client.job.get_job_list(params)

        self.assertEqual(result, expected)
        request.assert_called_once()
