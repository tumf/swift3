# Copyright (c) 2014 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
from mock import patch
from urllib import quote

from swift.common import swob
from swift.common.swob import Request
from swift.common.utils import json

from swift3.test.unit import Swift3TestCase
from swift3.etree import fromstring, tostring
from swift3.subresource import Owner, Grant, User, ACL, encode_acl, \
    decode_acl, ACLPublicRead
from swift3.test.unit.test_s3_acl import s3acl
from swift3.cfg import CONF
from swift3.utils import sysmeta_header
from swift3.request import MAX_32BIT_INT

xml = '<CompleteMultipartUpload>' \
    '<Part>' \
    '<PartNumber>1</PartNumber>' \
    '<ETag>HASH</ETag>' \
    '</Part>' \
    '<Part>' \
    '<PartNumber>2</PartNumber>' \
    '<ETag>"HASH"</ETag>' \
    '</Part>' \
    '</CompleteMultipartUpload>'

objects_template = \
    (('object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 100),
     ('object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 200))

multiparts_template = \
    (('object/X', '2014-05-07T19:47:50.592270', 'HASH', 1),
     ('object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 11),
     ('object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 21),
     ('object/Y', '2014-05-07T19:47:53.592270', 'HASH', 2),
     ('object/Y/1', '2014-05-07T19:47:54.592270', 'HASH', 12),
     ('object/Y/2', '2014-05-07T19:47:55.592270', 'HASH', 22),
     ('object/Z', '2014-05-07T19:47:56.592270', 'HASH', 3),
     ('object/Z/1', '2014-05-07T19:47:57.592270', 'HASH', 13),
     ('object/Z/2', '2014-05-07T19:47:58.592270', 'HASH', 23),
     ('subdir/object/Z', '2014-05-07T19:47:58.592270', 'HASH', 4),
     ('subdir/object/Z/1', '2014-05-07T19:47:58.592270', 'HASH', 41),
     ('subdir/object/Z/2', '2014-05-07T19:47:58.592270', 'HASH', 41))


class TestSwift3MultiUpload(Swift3TestCase):

    def setUp(self):
        super(TestSwift3MultiUpload, self).setUp()

        segment_bucket = '/v1/AUTH_test/bucket+segments'
        self.etag = '7dfa07a8e59ddbcd1dc84d4c4f82aea1'
        self.last_modified = 'Fri, 01 Apr 2014 12:00:00 GMT'
        put_headers = {'etag': self.etag, 'last-modified': self.last_modified}

        objects = map(lambda item: {'name': item[0], 'last_modified': item[1],
                                    'hash': item[2], 'bytes': item[3]},
                      objects_template)
        object_list = json.dumps(objects)

        self.swift.register('PUT',
                            '/v1/AUTH_test/bucket+segments',
                            swob.HTTPAccepted, {}, None)
        self.swift.register('GET', segment_bucket, swob.HTTPOk, {},
                            object_list)
        self.swift.register('HEAD', segment_bucket + '/object/X',
                            swob.HTTPOk, {'x-object-meta-foo': 'bar',
                                          'content-type': 'baz/quux'}, None)
        self.swift.register('PUT', segment_bucket + '/object/X',
                            swob.HTTPCreated, {}, None)
        self.swift.register('DELETE', segment_bucket + '/object/X',
                            swob.HTTPNoContent, {}, None)
        self.swift.register('GET', segment_bucket + '/object/invalid',
                            swob.HTTPNotFound, {}, None)
        self.swift.register('PUT', segment_bucket + '/object/X/1',
                            swob.HTTPCreated, put_headers, None)
        self.swift.register('DELETE', segment_bucket + '/object/X/1',
                            swob.HTTPNoContent, {}, None)
        self.swift.register('DELETE', segment_bucket + '/object/X/2',
                            swob.HTTPNoContent, {}, None)

        self.swift.register('HEAD', segment_bucket + '/object/Y',
                            swob.HTTPOk, {}, None)
        self.swift.register('PUT', segment_bucket + '/object/Y',
                            swob.HTTPCreated, {}, None)
        self.swift.register('DELETE', segment_bucket + '/object/Y',
                            swob.HTTPNoContent, {}, None)
        self.swift.register('PUT', segment_bucket + '/object/Y/1',
                            swob.HTTPCreated, {}, None)
        self.swift.register('DELETE', segment_bucket + '/object/Y/1',
                            swob.HTTPNoContent, {}, None)
        self.swift.register('DELETE', segment_bucket + '/object/Y/2',
                            swob.HTTPNoContent, {}, None)

        self.swift.register('HEAD', segment_bucket + '/object2/Z',
                            swob.HTTPOk, {}, None)
        self.swift.register('PUT', segment_bucket + '/object2/Z',
                            swob.HTTPCreated, {}, None)
        self.swift.register('DELETE', segment_bucket + '/object2/Z',
                            swob.HTTPNoContent, {}, None)
        self.swift.register('PUT', segment_bucket + '/object2/Z/1',
                            swob.HTTPCreated, {}, None)
        self.swift.register('DELETE', segment_bucket + '/object2/Z/1',
                            swob.HTTPNoContent, {}, None)
        self.swift.register('DELETE', segment_bucket + '/object2/Z/2',
                            swob.HTTPNoContent, {}, None)

    @s3acl
    def test_bucket_upload_part(self):
        req = Request.blank('/bucket?partNumber=1&uploadId=x',
                            environ={'REQUEST_METHOD': 'PUT'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidRequest')

    @s3acl
    def test_object_multipart_uploads_list(self):
        req = Request.blank('/bucket/object?uploads',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidRequest')

    @s3acl
    def test_bucket_multipart_uploads_initiate(self):
        req = Request.blank('/bucket?uploads',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidRequest')

    @s3acl
    def test_bucket_list_parts(self):
        req = Request.blank('/bucket?uploadId=x',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidRequest')

    @s3acl
    def test_bucket_multipart_uploads_abort(self):
        req = Request.blank('/bucket?uploadId=x',
                            environ={'REQUEST_METHOD': 'DELETE'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidRequest')

    @s3acl
    def test_bucket_multipart_uploads_complete(self):
        req = Request.blank('/bucket?uploadId=x',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidRequest')

    def _test_bucket_multipart_uploads_GET(self, query=None,
                                           multiparts=None):
        segment_bucket = '/v1/AUTH_test/bucket+segments'
        objects = multiparts or multiparts_template
        objects = map(lambda item: {'name': item[0], 'last_modified': item[1],
                                    'hash': item[2], 'bytes': item[3]},
                      objects)
        object_list = json.dumps(objects)
        self.swift.register('GET', segment_bucket, swob.HTTPOk, {},
                            object_list)

        query = '?uploads&' + query if query else '?uploads'
        req = Request.blank('/bucket/%s' % query,
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        return self.call_swift3(req)

    @s3acl
    def test_bucket_multipart_uploads_GET(self):
        status, headers, body = self._test_bucket_multipart_uploads_GET()
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(elem.find('Bucket').text, 'bucket')
        self.assertEquals(elem.find('KeyMarker').text, None)
        self.assertEquals(elem.find('UploadIdMarker').text, None)
        self.assertEquals(elem.find('NextUploadIdMarker').text, 'Z')
        self.assertEquals(elem.find('MaxUploads').text, '1000')
        self.assertEquals(elem.find('IsTruncated').text, 'false')
        self.assertEquals(len(elem.findall('Upload')), 4)
        objects = [(o[0], o[1][:-3] + 'Z') for o in multiparts_template]
        for u in elem.findall('Upload'):
            name = u.find('Key').text + '/' + u.find('UploadId').text
            initiated = u.find('Initiated').text
            self.assertTrue((name, initiated) in objects)
            self.assertEquals(u.find('Initiator/ID').text, 'test:tester')
            self.assertEquals(u.find('Initiator/DisplayName').text,
                              'test:tester')
            self.assertEquals(u.find('Owner/ID').text, 'test:tester')
            self.assertEquals(u.find('Owner/DisplayName').text, 'test:tester')
            self.assertEquals(u.find('StorageClass').text, 'STANDARD')
        self.assertEquals(status.split()[0], '200')

    @s3acl
    def test_bucket_multipart_uploads_GET_without_segment_bucket(self):
        segment_bucket = '/v1/AUTH_test/bucket+segments'
        self.swift.register('GET', segment_bucket, swob.HTTPNotFound, {}, '')

        req = Request.blank('/bucket?uploads',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})

        status, haeaders, body = self.call_swift3(req)

        self.assertEquals(status.split()[0], '200')
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(elem.find('Bucket').text, 'bucket')
        self.assertEquals(elem.find('KeyMarker').text, None)
        self.assertEquals(elem.find('UploadIdMarker').text, None)
        self.assertEquals(elem.find('NextUploadIdMarker').text, None)
        self.assertEquals(elem.find('MaxUploads').text, '1000')
        self.assertEquals(elem.find('IsTruncated').text, 'false')
        self.assertEquals(len(elem.findall('Upload')), 0)

    @s3acl
    @patch('swift3.request.get_container_info', lambda x, y: {'status': 404})
    def test_bucket_multipart_uploads_GET_without_bucket(self):
        self.swift.register('HEAD', '/v1/AUTH_test/bucket',
                            swob.HTTPNotFound, {}, '')
        req = Request.blank('/bucket?uploads',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, haeaders, body = self.call_swift3(req)
        self.assertEquals(status.split()[0], '404')
        self.assertEquals(self._get_error_code(body), 'NoSuchBucket')

    @s3acl
    def test_bucket_multipart_uploads_GET_encoding_type_error(self):
        query = 'encoding-type=xml'
        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    @s3acl
    def test_bucket_multipart_uploads_GET_maxuploads(self):
        query = 'max-uploads=2'
        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query)
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(len(elem.findall('Upload/UploadId')), 2)
        self.assertEquals(elem.find('NextKeyMarker').text, 'object')
        self.assertEquals(elem.find('NextUploadIdMarker').text, 'Y')
        self.assertEquals(elem.find('MaxUploads').text, '2')
        self.assertEquals(elem.find('IsTruncated').text, 'true')
        self.assertEquals(status.split()[0], '200')

    @s3acl
    def test_bucket_multipart_uploads_GET_str_maxuploads(self):
        query = 'max-uploads=invalid'
        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    @s3acl
    def test_bucket_multipart_uploads_GET_negative_maxuploads(self):
        query = 'max-uploads=-1'
        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    @s3acl
    def test_bucket_multipart_uploads_GET_maxuploads_over_default(self):
        query = 'max-uploads=1001'
        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query)
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(len(elem.findall('Upload/UploadId')), 4)
        self.assertEquals(elem.find('NextKeyMarker').text, 'subdir/object')
        self.assertEquals(elem.find('NextUploadIdMarker').text, 'Z')
        self.assertEquals(elem.find('MaxUploads').text, '1000')
        self.assertEquals(elem.find('IsTruncated').text, 'false')
        self.assertEquals(status.split()[0], '200')

    @s3acl
    def test_bucket_multipart_uploads_GET_maxuploads_over_max_32bit_int(self):
        query = 'max-uploads=%s' % (MAX_32BIT_INT + 1)
        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    @s3acl
    def test_bucket_multipart_uploads_GET_with_id_and_key_marker(self):
        query = 'upload-id-marker=Y&key-marker=object'
        multiparts = \
            (('object/Y', '2014-05-07T19:47:53.592270', 'HASH', 2),
             ('object/Y/1', '2014-05-07T19:47:54.592270', 'HASH', 12),
             ('object/Y/2', '2014-05-07T19:47:55.592270', 'HASH', 22))

        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query, multiparts)
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(elem.find('KeyMarker').text, 'object')
        self.assertEquals(elem.find('UploadIdMarker').text, 'Y')
        self.assertEquals(len(elem.findall('Upload')), 1)
        objects = [(o[0], o[1][:-3] + 'Z') for o in multiparts]
        for u in elem.findall('Upload'):
            name = u.find('Key').text + '/' + u.find('UploadId').text
            initiated = u.find('Initiated').text
            self.assertTrue((name, initiated) in objects)
        self.assertEquals(status.split()[0], '200')

        _, path, _ = self.swift.calls_with_headers[-1]
        path, query_string = path.split('?', 1)
        query = {}
        for q in query_string.split('&'):
            key, arg = q.split('=')
            query[key] = arg
        self.assertEquals(query['format'], 'json')
        self.assertEquals(query['limit'], '1001')
        self.assertEquals(query['marker'], 'object/Y')

    @s3acl
    def test_bucket_multipart_uploads_GET_with_key_marker(self):
        query = 'key-marker=object'
        multiparts = \
            (('object/X', '2014-05-07T19:47:50.592270', 'HASH', 1),
             ('object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 11),
             ('object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 21),
             ('object/Y', '2014-05-07T19:47:53.592270', 'HASH', 2),
             ('object/Y/1', '2014-05-07T19:47:54.592270', 'HASH', 12),
             ('object/Y/2', '2014-05-07T19:47:55.592270', 'HASH', 22))
        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query, multiparts)
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(elem.find('KeyMarker').text, 'object')
        self.assertEquals(elem.find('NextKeyMarker').text, 'object')
        self.assertEquals(elem.find('NextUploadIdMarker').text, 'Y')
        self.assertEquals(len(elem.findall('Upload')), 2)
        objects = [(o[0], o[1][:-3] + 'Z') for o in multiparts]
        for u in elem.findall('Upload'):
            name = u.find('Key').text + '/' + u.find('UploadId').text
            initiated = u.find('Initiated').text
            self.assertTrue((name, initiated) in objects)
        self.assertEquals(status.split()[0], '200')

        _, path, _ = self.swift.calls_with_headers[-1]
        path, query_string = path.split('?', 1)
        query = {}
        for q in query_string.split('&'):
            key, arg = q.split('=')
            query[key] = arg
        self.assertEquals(query['format'], 'json')
        self.assertEquals(query['limit'], '1001')
        self.assertEquals(query['marker'], quote('object/~'))

    @s3acl
    def test_bucket_multipart_uploads_GET_with_prefix(self):
        query = 'prefix=X'
        multiparts = \
            (('object/X', '2014-05-07T19:47:50.592270', 'HASH', 1),
             ('object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 11),
             ('object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 21))
        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query, multiparts)
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(len(elem.findall('Upload')), 1)
        objects = [(o[0], o[1][:-3] + 'Z') for o in multiparts]
        for u in elem.findall('Upload'):
            name = u.find('Key').text + '/' + u.find('UploadId').text
            initiated = u.find('Initiated').text
            self.assertTrue((name, initiated) in objects)
        self.assertEquals(status.split()[0], '200')

        _, path, _ = self.swift.calls_with_headers[-1]
        path, query_string = path.split('?', 1)
        query = {}
        for q in query_string.split('&'):
            key, arg = q.split('=')
            query[key] = arg
        self.assertEquals(query['format'], 'json')
        self.assertEquals(query['limit'], '1001')
        self.assertEquals(query['prefix'], 'X')

    @s3acl
    def test_bucket_multipart_uploads_GET_with_delimiter(self):
        query = 'delimiter=/'
        multiparts = \
            (('object/X', '2014-05-07T19:47:50.592270', 'HASH', 1),
             ('object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 11),
             ('object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 21),
             ('object/Y', '2014-05-07T19:47:50.592270', 'HASH', 2),
             ('object/Y/1', '2014-05-07T19:47:51.592270', 'HASH', 21),
             ('object/Y/2', '2014-05-07T19:47:52.592270', 'HASH', 22),
             ('object/Z', '2014-05-07T19:47:50.592270', 'HASH', 3),
             ('object/Z/1', '2014-05-07T19:47:51.592270', 'HASH', 31),
             ('object/Z/2', '2014-05-07T19:47:52.592270', 'HASH', 32),
             ('subdir/object/X', '2014-05-07T19:47:50.592270', 'HASH', 4),
             ('subdir/object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 41),
             ('subdir/object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 42),
             ('subdir/object/Y', '2014-05-07T19:47:50.592270', 'HASH', 5),
             ('subdir/object/Y/1', '2014-05-07T19:47:51.592270', 'HASH', 51),
             ('subdir/object/Y/2', '2014-05-07T19:47:52.592270', 'HASH', 52),
             ('subdir2/object/Z', '2014-05-07T19:47:50.592270', 'HASH', 6),
             ('subdir2/object/Z/1', '2014-05-07T19:47:51.592270', 'HASH', 61),
             ('subdir2/object/Z/2', '2014-05-07T19:47:52.592270', 'HASH', 62))

        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query, multiparts)
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(len(elem.findall('Upload')), 3)
        self.assertEquals(len(elem.findall('CommonPrefixes')), 2)
        objects = [(o[0], o[1][:-3] + 'Z') for o in multiparts
                   if o[0].startswith('o')]
        prefixes = set([o[0].split('/')[0] + '/' for o in multiparts
                       if o[0].startswith('s')])
        for u in elem.findall('Upload'):
            name = u.find('Key').text + '/' + u.find('UploadId').text
            initiated = u.find('Initiated').text
            self.assertTrue((name, initiated) in objects)
        for p in elem.findall('CommonPrefixes'):
            prefix = p.find('Prefix').text
            self.assertTrue(prefix in prefixes)

        self.assertEquals(status.split()[0], '200')
        _, path, _ = self.swift.calls_with_headers[-1]
        path, query_string = path.split('?', 1)
        query = {}
        for q in query_string.split('&'):
            key, arg = q.split('=')
            query[key] = arg
        self.assertEquals(query['format'], 'json')
        self.assertEquals(query['limit'], '1001')
        self.assertTrue(query.get('delimiter') is None)

    @s3acl
    def test_bucket_multipart_uploads_GET_with_multi_chars_delimiter(self):
        query = 'delimiter=subdir'
        multiparts = \
            (('object/X', '2014-05-07T19:47:50.592270', 'HASH', 1),
             ('object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 11),
             ('object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 21),
             ('dir/subdir/object/X', '2014-05-07T19:47:50.592270',
              'HASH', 3),
             ('dir/subdir/object/X/1', '2014-05-07T19:47:51.592270',
              'HASH', 31),
             ('dir/subdir/object/X/2', '2014-05-07T19:47:52.592270',
              'HASH', 32),
             ('subdir/object/X', '2014-05-07T19:47:50.592270', 'HASH', 4),
             ('subdir/object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 41),
             ('subdir/object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 42),
             ('subdir/object/Y', '2014-05-07T19:47:50.592270', 'HASH', 5),
             ('subdir/object/Y/1', '2014-05-07T19:47:51.592270', 'HASH', 51),
             ('subdir/object/Y/2', '2014-05-07T19:47:52.592270', 'HASH', 52),
             ('subdir2/object/Z', '2014-05-07T19:47:50.592270', 'HASH', 6),
             ('subdir2/object/Z/1', '2014-05-07T19:47:51.592270', 'HASH', 61),
             ('subdir2/object/Z/2', '2014-05-07T19:47:52.592270', 'HASH', 62))

        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query, multiparts)
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(len(elem.findall('Upload')), 1)
        self.assertEquals(len(elem.findall('CommonPrefixes')), 2)
        objects = [(o[0], o[1][:-3] + 'Z') for o in multiparts
                   if o[0].startswith('object')]
        prefixes = ('dir/subdir', 'subdir')
        for u in elem.findall('Upload'):
            name = u.find('Key').text + '/' + u.find('UploadId').text
            initiated = u.find('Initiated').text
            self.assertTrue((name, initiated) in objects)
        for p in elem.findall('CommonPrefixes'):
            prefix = p.find('Prefix').text
            self.assertTrue(prefix in prefixes)

        self.assertEquals(status.split()[0], '200')
        _, path, _ = self.swift.calls_with_headers[-1]
        path, query_string = path.split('?', 1)
        query = {}
        for q in query_string.split('&'):
            key, arg = q.split('=')
            query[key] = arg
        self.assertEquals(query['format'], 'json')
        self.assertEquals(query['limit'], '1001')
        self.assertTrue(query.get('delimiter') is None)

    @s3acl
    def test_bucket_multipart_uploads_GET_with_prefix_and_delimiter(self):
        query = 'prefix=dir/&delimiter=/'
        multiparts = \
            (('dir/subdir/object/X', '2014-05-07T19:47:50.592270',
              'HASH', 4),
             ('dir/subdir/object/X/1', '2014-05-07T19:47:51.592270',
              'HASH', 41),
             ('dir/subdir/object/X/2', '2014-05-07T19:47:52.592270',
              'HASH', 42),
             ('dir/object/X', '2014-05-07T19:47:50.592270', 'HASH', 5),
             ('dir/object/X/1', '2014-05-07T19:47:51.592270', 'HASH', 51),
             ('dir/object/X/2', '2014-05-07T19:47:52.592270', 'HASH', 52))

        status, headers, body = \
            self._test_bucket_multipart_uploads_GET(query, multiparts)
        elem = fromstring(body, 'ListMultipartUploadsResult')
        self.assertEquals(len(elem.findall('Upload')), 1)
        self.assertEquals(len(elem.findall('CommonPrefixes')), 1)
        objects = [(o[0], o[1][:-3] + 'Z') for o in multiparts
                   if o[0].startswith('dir/o')]
        prefixes = ['dir/subdir/']
        for u in elem.findall('Upload'):
            name = u.find('Key').text + '/' + u.find('UploadId').text
            initiated = u.find('Initiated').text
            self.assertTrue((name, initiated) in objects)
        for p in elem.findall('CommonPrefixes'):
            prefix = p.find('Prefix').text
            self.assertTrue(prefix in prefixes)

        self.assertEquals(status.split()[0], '200')
        _, path, _ = self.swift.calls_with_headers[-1]
        path, query_string = path.split('?', 1)
        query = {}
        for q in query_string.split('&'):
            key, arg = q.split('=')
            query[key] = arg
        self.assertEquals(query['format'], 'json')
        self.assertEquals(query['limit'], '1001')
        self.assertEquals(query['prefix'], 'dir/')
        self.assertTrue(query.get('delimiter') is None)

    @patch('swift3.controllers.multi_upload.unique_id', lambda: 'X')
    def test_object_multipart_upload_initiate(self):
        req = Request.blank('/bucket/object?uploads',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization':
                                     'AWS test:tester:hmac',
                                     'x-amz-meta-foo': 'bar'})
        status, headers, body = self.call_swift3(req)
        fromstring(body, 'InitiateMultipartUploadResult')
        self.assertEquals(status.split()[0], '200')

        _, _, req_headers = self.swift.calls_with_headers[-1]
        self.assertEquals(req_headers.get('X-Object-Meta-Foo'), 'bar')

    @s3acl(s3acl_only=True)
    @patch('swift3.controllers.multi_upload.unique_id', lambda: 'X')
    def test_object_multipart_upload_initiate_s3acl(self):
        req = Request.blank('/bucket/object?uploads',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization':
                                     'AWS test:tester:hmac',
                                     'x-amz-acl': 'public-read',
                                     'x-amz-meta-foo': 'bar'})
        status, headers, body = self.call_swift3(req)
        fromstring(body, 'InitiateMultipartUploadResult')
        self.assertEquals(status.split()[0], '200')

        _, _, req_headers = self.swift.calls_with_headers[-1]
        self.assertEquals(req_headers.get('X-Object-Meta-Foo'), 'bar')
        tmpacl_header = req_headers.get(sysmeta_header('object', 'tmpacl'))
        self.assertTrue(tmpacl_header)
        acl_header = encode_acl('object',
                                ACLPublicRead(Owner('test:tester',
                                                    'test:tester')))
        self.assertEquals(acl_header.get(sysmeta_header('object', 'acl')),
                          tmpacl_header)

    @patch('swift3.controllers.multi_upload.unique_id', lambda: 'X')
    def test_object_multipart_upload_initiate_without_bucket(self):
        self.swift.register('HEAD', '/v1/AUTH_test/bucket',
                            swob.HTTPNotFound, {}, None)
        req = Request.blank('/bucket/object?uploads',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization':
                                     'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(status.split()[0], '404')
        self.assertEquals(self._get_error_code(body), 'NoSuchBucket')

    @s3acl
    def test_object_multipart_upload_complete_error(self):
        malformed_xml = 'malformed_XML'
        req = Request.blank('/bucket/object?uploadId=X',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body=malformed_xml)
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'MalformedXML')

        # without target bucket
        req = Request.blank('/nobucket/object?uploadId=X',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body=xml)
        with patch('swift3.request.get_container_info',
                   lambda x, y: {'status': 404}):
            self.swift.register('HEAD', '/v1/AUTH_test/nobucket',
                                swob.HTTPNotFound, {}, None)
            status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'NoSuchBucket')

    def test_object_multipart_upload_complete(self):
        req = Request.blank('/bucket/object?uploadId=X',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body=xml)
        status, headers, body = self.call_swift3(req)
        fromstring(body, 'CompleteMultipartUploadResult')
        self.assertEquals(status.split()[0], '200')

        _, _, headers = self.swift.calls_with_headers[-2]
        self.assertEquals(headers.get('X-Object-Meta-Foo'), 'bar')
        self.assertEquals(headers.get('Content-Type'), 'baz/quux')

    @s3acl(s3acl_only=True)
    def test_object_multipart_upload_complete_s3acl(self):
        acl_headers = encode_acl('object', ACLPublicRead(Owner('test:tester',
                                                               'test:tester')))
        headers = {}
        headers[sysmeta_header('object', 'tmpacl')] = \
            acl_headers.get(sysmeta_header('object', 'acl'))
        headers['X-Object-Meta-Foo'] = 'bar'
        headers['Content-Type'] = 'baz/quux'
        self.swift.register('HEAD', '/v1/AUTH_test/bucket+segments/object/X',
                            swob.HTTPOk, headers, None)
        req = Request.blank('/bucket/object?uploadId=X',
                            environ={'REQUEST_METHOD': 'POST'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body=xml)
        status, headers, body = self.call_swift3(req)
        fromstring(body, 'CompleteMultipartUploadResult')
        self.assertEquals(status.split()[0], '200')

        _, _, headers = self.swift.calls_with_headers[-2]
        self.assertEquals(headers.get('X-Object-Meta-Foo'), 'bar')
        self.assertEquals(headers.get('Content-Type'), 'baz/quux')
        self.assertEquals(tostring(ACLPublicRead(Owner('test:tester',
                                                       'test:tester')).elem()),
                          tostring(decode_acl('object', headers).elem()))

    @s3acl
    def test_object_multipart_upload_abort_error(self):
        req = Request.blank('/bucket/object?uploadId=invalid',
                            environ={'REQUEST_METHOD': 'DELETE'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'NoSuchUpload')

        # without target bucket
        req = Request.blank('/nobucket/object?uploadId=X',
                            environ={'REQUEST_METHOD': 'DELETE'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        with patch('swift3.request.get_container_info',
                   lambda x, y: {'status': 404}):
            self.swift.register('HEAD', '/v1/AUTH_test/nobucket',
                                swob.HTTPNotFound, {}, None)
            status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'NoSuchBucket')

    @s3acl
    def test_object_multipart_upload_abort(self):
        req = Request.blank('/bucket/object?uploadId=X',
                            environ={'REQUEST_METHOD': 'DELETE'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(status.split()[0], '204')

    @s3acl
    @patch('swift3.request.get_container_info', lambda x, y: {'status': 204})
    def test_object_upload_part_error(self):
        # without upload id
        req = Request.blank('/bucket/object?partNumber=1',
                            environ={'REQUEST_METHOD': 'PUT'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body='part object')
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

        # invalid part number
        req = Request.blank('/bucket/object?partNumber=invalid&uploadId=X',
                            environ={'REQUEST_METHOD': 'PUT'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body='part object')
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

        # part number must be > 0
        req = Request.blank('/bucket/object?partNumber=0&uploadId=X',
                            environ={'REQUEST_METHOD': 'PUT'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body='part object')
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

        # part number must be < 1000
        req = Request.blank('/bucket/object?partNumber=1001&uploadId=X',
                            environ={'REQUEST_METHOD': 'PUT'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body='part object')
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

        # without target bucket
        req = Request.blank('/nobucket/object?partNumber=1&uploadId=X',
                            environ={'REQUEST_METHOD': 'PUT'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body='part object')
        with patch('swift3.request.get_container_info',
                   lambda x, y: {'status': 404}):
            self.swift.register('HEAD', '/v1/AUTH_test/nobucket',
                                swob.HTTPNotFound, {}, None)
            status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'NoSuchBucket')

    @s3acl
    def test_object_upload_part(self):
        req = Request.blank('/bucket/object?partNumber=1&uploadId=X',
                            environ={'REQUEST_METHOD': 'PUT'},
                            headers={'Authorization': 'AWS test:tester:hmac'},
                            body='part object')
        status, headers, body = self.call_swift3(req)
        self.assertEquals(status.split()[0], '200')

    @s3acl
    def test_object_list_parts_error(self):
        req = Request.blank('/bucket/object?uploadId=invalid',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'NoSuchUpload')

        # without target bucket
        req = Request.blank('/nobucket/object?uploadId=X',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        with patch('swift3.request.get_container_info',
                   lambda x, y: {'status': 404}):
            self.swift.register('HEAD', '/v1/AUTH_test/nobucket',
                                swob.HTTPNotFound, {}, None)
            status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'NoSuchBucket')

    @s3acl
    def test_object_list_parts(self):
        req = Request.blank('/bucket/object?uploadId=X',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        elem = fromstring(body, 'ListPartsResult')
        self.assertEquals(elem.find('Bucket').text, 'bucket')
        self.assertEquals(elem.find('Key').text, 'object')
        self.assertEquals(elem.find('UploadId').text, 'X')
        self.assertEquals(elem.find('Initiator/ID').text, 'test:tester')
        self.assertEquals(elem.find('Initiator/ID').text, 'test:tester')
        self.assertEquals(elem.find('Owner/ID').text, 'test:tester')
        self.assertEquals(elem.find('Owner/ID').text, 'test:tester')
        self.assertEquals(elem.find('StorageClass').text, 'STANDARD')
        self.assertEquals(elem.find('PartNumberMarker').text, '0')
        self.assertEquals(elem.find('NextPartNumberMarker').text, '2')
        self.assertEquals(elem.find('MaxParts').text, '1000')
        self.assertEquals(elem.find('IsTruncated').text, 'false')
        self.assertEquals(len(elem.findall('Part')), 2)
        for p in elem.findall('Part'):
            partnum = int(p.find('PartNumber').text)
            self.assertEquals(p.find('LastModified').text,
                              objects_template[partnum - 1][1][:-3]
                              + 'Z')
            self.assertEquals(p.find('ETag').text.strip(),
                              '"%s"' % objects_template[partnum - 1][2])
            self.assertEquals(p.find('Size').text,
                              str(objects_template[partnum - 1][3]))
        self.assertEquals(status.split()[0], '200')

    def test_object_list_parts_encoding_type(self):
        self.swift.register('HEAD', '/v1/AUTH_test/bucket+segments/object@@/X',
                            swob.HTTPOk, {}, None)
        req = Request.blank('/bucket/object@@?uploadId=X&encoding-type=url',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        elem = fromstring(body, 'ListPartsResult')
        self.assertEquals(elem.find('Key').text, quote('object@@'))
        self.assertEquals(elem.find('EncodingType').text, 'url')
        self.assertEquals(status.split()[0], '200')

    def test_object_list_parts_without_encoding_type(self):
        self.swift.register('HEAD', '/v1/AUTH_test/bucket+segments/object@@/X',
                            swob.HTTPOk, {}, None)
        req = Request.blank('/bucket/object@@?uploadId=X',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        elem = fromstring(body, 'ListPartsResult')
        self.assertEquals(elem.find('Key').text, 'object@@')
        self.assertEquals(status.split()[0], '200')

    def test_object_list_parts_encoding_type_error(self):
        req = Request.blank('/bucket/object?uploadId=X&encoding-type=xml',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    def test_object_list_parts_max_parts(self):
        req = Request.blank('/bucket/object?uploadId=X&max-parts=1',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        elem = fromstring(body, 'ListPartsResult')
        self.assertEquals(elem.find('IsTruncated').text, 'true')
        self.assertEquals(len(elem.findall('Part')), 1)
        self.assertEquals(status.split()[0], '200')

    def test_object_list_parts_str_max_parts(self):
        req = Request.blank('/bucket/object?uploadId=X&max-parts=invalid',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    def test_object_list_parts_negative_max_parts(self):
        req = Request.blank('/bucket/object?uploadId=X&max-parts=-1',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    def test_object_list_parts_over_max_parts(self):
        req = Request.blank('/bucket/object?uploadId=X&max-parts=%d' %
                            (CONF.max_parts_listing + 1),
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        elem = fromstring(body, 'ListPartsResult')
        self.assertEquals(elem.find('Bucket').text, 'bucket')
        self.assertEquals(elem.find('Key').text, 'object')
        self.assertEquals(elem.find('UploadId').text, 'X')
        self.assertEquals(elem.find('Initiator/ID').text, 'test:tester')
        self.assertEquals(elem.find('Owner/ID').text, 'test:tester')
        self.assertEquals(elem.find('StorageClass').text, 'STANDARD')
        self.assertEquals(elem.find('PartNumberMarker').text, '0')
        self.assertEquals(elem.find('NextPartNumberMarker').text, '2')
        self.assertEquals(elem.find('MaxParts').text, '1000')
        self.assertEquals(elem.find('IsTruncated').text, 'false')
        self.assertEquals(len(elem.findall('Part')), 2)
        for p in elem.findall('Part'):
            partnum = int(p.find('PartNumber').text)
            self.assertEquals(p.find('LastModified').text,
                              objects_template[partnum - 1][1][:-3]
                              + 'Z')
            self.assertEquals(p.find('ETag').text,
                              '"%s"' % objects_template[partnum - 1][2])
            self.assertEquals(p.find('Size').text,
                              str(objects_template[partnum - 1][3]))
        self.assertEquals(status.split()[0], '200')

    def test_object_list_parts_over_max_32bit_int(self):
        req = Request.blank('/bucket/object?uploadId=X&max-parts=%d' %
                            (MAX_32BIT_INT + 1),
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    def test_object_list_parts_with_part_number_marker(self):
        req = Request.blank('/bucket/object?uploadId=X&'
                            'part-number-marker=1',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        elem = fromstring(body, 'ListPartsResult')
        self.assertEquals(len(elem.findall('Part')), 1)
        self.assertEquals(elem.find('Part/PartNumber').text, '2')
        self.assertEquals(elem.find('PartNumberMarker').text, '1')
        self.assertEquals(status.split()[0], '200')

    def test_object_list_parts_str_part_number_marker(self):
        req = Request.blank('/bucket/object?uploadId=X&part-number-marker='
                            'invalid',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    def test_object_list_parts_negative_part_number_marker(self):
        req = Request.blank('/bucket/object?uploadId=X&part-number-marker='
                            '-1',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    def test_object_list_parts_over_part_number_marker(self):
        part_number_marker = str(CONF.max_upload_part_num + 1)
        req = Request.blank('/bucket/object?uploadId=X&'
                            'part-number-marker=%s' % part_number_marker,
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        elem = fromstring(body, 'ListPartsResult')
        self.assertEquals(len(elem.findall('Part')), 0)
        self.assertEquals(elem.find('PartNumberMarker').text,
                          part_number_marker)
        self.assertEquals(status.split()[0], '200')

    def test_object_list_parts_over_max_32bit_int_part_number_marker(self):
        req = Request.blank('/bucket/object?uploadId=X&part-number-marker='
                            '%s' % ((MAX_32BIT_INT + 1)),
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        self.assertEquals(self._get_error_code(body), 'InvalidArgument')

    def test_object_list_parts_same_max_marts_as_objects_num(self):
        req = Request.blank('/bucket/object?uploadId=X&max-parts=2',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'AWS test:tester:hmac'})
        status, headers, body = self.call_swift3(req)
        elem = fromstring(body, 'ListPartsResult')
        self.assertEquals(len(elem.findall('Part')), 2)
        self.assertEquals(status.split()[0], '200')

    def _test_for_s3acl(self, method, query, account, hasObj=True, body=None):
        path = '/bucket%s' % ('/object' + query if hasObj else query)
        req = Request.blank(path,
                            environ={'REQUEST_METHOD': method},
                            headers={'Authorization': 'AWS %s:hmac' % account},
                            body=body)
        return self.call_swift3(req)

    @s3acl(s3acl_only=True)
    def test_upload_part_acl_without_permission(self):
        status, headers, body = \
            self._test_for_s3acl('PUT', '?partNumber=1&uploadId=X',
                                 'test:other')
        self.assertEquals(status.split()[0], '403')

    @s3acl(s3acl_only=True)
    def test_upload_part_acl_with_write_permission(self):
        status, headers, body = \
            self._test_for_s3acl('PUT', '?partNumber=1&uploadId=X',
                                 'test:write')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_upload_part_acl_with_fullcontrol_permission(self):
        status, headers, body = \
            self._test_for_s3acl('PUT', '?partNumber=1&uploadId=X',
                                 'test:full_control')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_list_multipart_uploads_acl_without_permission(self):
        status, headers, body = \
            self._test_for_s3acl('GET', '?uploads', 'test:other',
                                 hasObj=False)
        self.assertEquals(status.split()[0], '403')

    @s3acl(s3acl_only=True)
    def test_list_multipart_uploads_acl_with_read_permission(self):
        status, headers, body = \
            self._test_for_s3acl('GET', '?uploads', 'test:read',
                                 hasObj=False)
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_list_multipart_uploads_acl_with_fullcontrol_permission(self):
        status, headers, body = \
            self._test_for_s3acl('GET', '?uploads', 'test:full_control',
                                 hasObj=False)
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    @patch('swift3.controllers.multi_upload.unique_id', lambda: 'X')
    def test_initiate_multipart_upload_acl_without_permission(self):
        status, headers, body = \
            self._test_for_s3acl('POST', '?uploads', 'test:other')
        self.assertEquals(status.split()[0], '403')

    @s3acl(s3acl_only=True)
    @patch('swift3.controllers.multi_upload.unique_id', lambda: 'X')
    def test_initiate_multipart_upload_acl_with_write_permission(self):
        status, headers, body = \
            self._test_for_s3acl('POST', '?uploads', 'test:write')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    @patch('swift3.controllers.multi_upload.unique_id', lambda: 'X')
    def test_initiate_multipart_upload_acl_with_fullcontrol_permission(self):
        status, headers, body = \
            self._test_for_s3acl('POST', '?uploads', 'test:full_control')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_list_parts_acl_without_permission(self):
        status, headers, body = \
            self._test_for_s3acl('GET', '?uploadId=X', 'test:other')
        self.assertEquals(status.split()[0], '403')

    @s3acl(s3acl_only=True)
    def test_list_parts_acl_with_read_permission(self):
        status, headers, body = \
            self._test_for_s3acl('GET', '?uploadId=X', 'test:read')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_list_parts_acl_with_fullcontrol_permission(self):
        status, headers, body = \
            self._test_for_s3acl('GET', '?uploadId=X', 'test:full_control')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_abort_multipart_upload_acl_without_permission(self):
        status, headers, body = \
            self._test_for_s3acl('DELETE', '?uploadId=X', 'test:other')
        self.assertEquals(status.split()[0], '403')

    @s3acl(s3acl_only=True)
    def test_abort_multipart_upload_acl_with_write_permission(self):
        status, headers, body = \
            self._test_for_s3acl('DELETE', '?uploadId=X', 'test:write')
        self.assertEquals(status.split()[0], '204')

    @s3acl(s3acl_only=True)
    def test_abort_multipart_upload_acl_with_fullcontrol_permission(self):
        status, headers, body = \
            self._test_for_s3acl('DELETE', '?uploadId=X', 'test:full_control')
        self.assertEquals(status.split()[0], '204')

    @s3acl(s3acl_only=True)
    def test_complete_multipart_upload_acl_without_permission(self):
        status, headers, body = \
            self._test_for_s3acl('POST', '?uploadId=X', 'test:other',
                                 body=xml)
        self.assertEquals(status.split()[0], '403')

    @s3acl(s3acl_only=True)
    def test_complete_multipart_upload_acl_with_write_permission(self):
        status, headers, body = \
            self._test_for_s3acl('POST', '?uploadId=X', 'test:write',
                                 body=xml)
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_complete_multipart_upload_acl_with_fullcontrol_permission(self):
        status, headers, body = \
            self._test_for_s3acl('POST', '?uploadId=X', 'test:full_control',
                                 body=xml)
        self.assertEquals(status.split()[0], '200')

    def _test_copy_for_s3acl(self, account, src_permission=None,
                             src_path='/src_bucket/src_obj',
                             head_resp=swob.HTTPOk, put_header={}):
        owner = 'test:tester'
        grants = [Grant(User(account), src_permission)] \
            if src_permission else [Grant(User(owner), 'FULL_CONTROL')]
        src_o_headers = encode_acl('object', ACL(Owner(owner, owner), grants))
        src_o_headers.update({'last-modified': self.last_modified})
        self.swift.register('HEAD', '/v1/AUTH_test/src_bucket/src_obj',
                            head_resp, src_o_headers, None)

        put_headers = {'Authorization': 'AWS %s:hmac' % account,
                       'X-Amz-Copy-Source': src_path}
        put_headers.update(put_header)
        req = Request.blank(
            '/bucket/object?partNumber=1&uploadId=X',
            environ={'REQUEST_METHOD': 'PUT'},
            headers=put_headers)
        return self.call_swift3(req)

    @s3acl
    def test_upload_part_copy(self):
        last_modified = '2014-04-01T12:00:00'
        status, headers, body = \
            self._test_copy_for_s3acl('test:tester')
        self.assertEquals(status.split()[0], '200')
        self.assertEquals(headers['Content-Type'], 'application/xml')
        self.assertTrue(headers.get('etag') is None)
        elem = fromstring(body, 'CopyPartResult')
        self.assertEquals(elem.find('LastModified').text, last_modified)
        self.assertEquals(elem.find('ETag').text, '"%s"' % self.etag)

        _, _, headers = self.swift.calls_with_headers[-1]
        self.assertEquals(headers['X-Copy-From'], '/src_bucket/src_obj')
        self.assertEquals(headers['Content-Length'], '0')

    @s3acl(s3acl_only=True)
    def test_upload_part_copy_acl_with_owner_permission(self):
        status, headers, body = \
            self._test_copy_for_s3acl('test:tester')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_upload_part_copy_acl_without_permission(self):
        status, headers, body = \
            self._test_copy_for_s3acl('test:other', 'READ')
        self.assertEquals(status.split()[0], '403')

    @s3acl(s3acl_only=True)
    def test_upload_part_copy_acl_with_write_permission(self):
        status, headers, body = \
            self._test_copy_for_s3acl('test:write', 'READ')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_upload_part_copy_acl_with_fullcontrol_permission(self):
        status, headers, body = \
            self._test_copy_for_s3acl('test:full_control', 'READ')
        self.assertEquals(status.split()[0], '200')

    @s3acl(s3acl_only=True)
    def test_upload_part_copy_acl_without_src_permission(self):
        status, headers, body = \
            self._test_copy_for_s3acl('test:write', 'WRITE')
        self.assertEquals(status.split()[0], '403')

    @s3acl(s3acl_only=True)
    def test_upload_part_copy_acl_invalid_source(self):
        status, headers, body = \
            self._test_copy_for_s3acl('test:write', 'WRITE', '')
        self.assertEquals(status.split()[0], '400')

        status, headers, body = \
            self._test_copy_for_s3acl('test:write', 'WRITE', '/')
        self.assertEquals(status.split()[0], '400')

        status, headers, body = \
            self._test_copy_for_s3acl('test:write', 'WRITE', '/bucket')
        self.assertEquals(status.split()[0], '400')

        status, headers, body = \
            self._test_copy_for_s3acl('test:write', 'WRITE', '/bucket/')
        self.assertEquals(status.split()[0], '400')

    @s3acl
    def test_upload_part_copy_headers_error(self):
        account = 'test:tester'
        etag = '7dfa07a8e59ddbcd1dc84d4c4f82aea1'
        last_modified_since = 'Fri, 01 Apr 2014 12:00:00 GMT'

        header = {'X-Amz-Copy-Source-If-Match': etag}
        status, header, body = \
            self._test_copy_for_s3acl(account,
                                      head_resp=swob.HTTPPreconditionFailed,
                                      put_header=header)
        self.assertEquals(self._get_error_code(body), 'PreconditionFailed')

        header = {'X-Amz-Copy-Source-If-None-Match': etag}
        status, header, body = \
            self._test_copy_for_s3acl(account,
                                      head_resp=swob.HTTPNotModified,
                                      put_header=header)
        self.assertEquals(self._get_error_code(body), 'PreconditionFailed')

        header = {'X-Amz-Copy-Source-If-Modified-Since': last_modified_since}
        status, header, body = \
            self._test_copy_for_s3acl(account,
                                      head_resp=swob.HTTPNotModified,
                                      put_header=header)
        self.assertEquals(self._get_error_code(body), 'PreconditionFailed')

        header = \
            {'X-Amz-Copy-Source-If-Unmodified-Since': last_modified_since}
        status, header, body = \
            self._test_copy_for_s3acl(account,
                                      head_resp=swob.HTTPPreconditionFailed,
                                      put_header=header)
        self.assertEquals(self._get_error_code(body), 'PreconditionFailed')

    def test_upload_part_copy_headers_with_match(self):
        account = 'test:tester'
        etag = '7dfa07a8e59ddbcd1dc84d4c4f82aea1'
        last_modified_since = 'Fri, 01 Apr 2014 11:00:00 GMT'

        header = {'X-Amz-Copy-Source-If-Match': etag,
                  'X-Amz-Copy-Source-If-Modified-Since': last_modified_since}
        status, header, body = \
            self._test_copy_for_s3acl(account, put_header=header)

        self.assertEquals(status.split()[0], '200')

        self.assertEquals(len(self.swift.calls_with_headers), 4)
        _, _, headers = self.swift.calls_with_headers[-2]
        self.assertEquals(headers['If-Match'], etag)
        self.assertEquals(headers['If-Modified-Since'], last_modified_since)
        _, _, headers = self.swift.calls_with_headers[-1]
        self.assertTrue(headers.get('If-Match') is None)
        self.assertTrue(headers.get('If-Modified-Since') is None)
        _, _, headers = self.swift.calls_with_headers[0]
        self.assertTrue(headers.get('If-Match') is None)
        self.assertTrue(headers.get('If-Modified-Since') is None)

    @s3acl(s3acl_only=True)
    def test_upload_part_copy_headers_with_match_and_s3acl(self):
        account = 'test:tester'
        etag = '7dfa07a8e59ddbcd1dc84d4c4f82aea1'
        last_modified_since = 'Fri, 01 Apr 2014 11:00:00 GMT'

        header = {'X-Amz-Copy-Source-If-Match': etag,
                  'X-Amz-Copy-Source-If-Modified-Since': last_modified_since}
        status, header, body = \
            self._test_copy_for_s3acl(account, put_header=header)

        self.assertEquals(status.split()[0], '200')
        self.assertEquals(len(self.swift.calls_with_headers), 4)
        # Before the check of the copy source in the case of s3acl is valid,
        # Swift3 check the bucket write permissions and the object existence
        # of the destination.
        _, _, headers = self.swift.calls_with_headers[-3]
        self.assertTrue(headers.get('If-Match') is None)
        self.assertTrue(headers.get('If-Modified-Since') is None)
        _, _, headers = self.swift.calls_with_headers[-2]
        self.assertEquals(headers['If-Match'], etag)
        self.assertEquals(headers['If-Modified-Since'], last_modified_since)
        _, _, headers = self.swift.calls_with_headers[-1]
        self.assertTrue(headers.get('If-Match') is None)
        self.assertTrue(headers.get('If-Modified-Since') is None)
        _, _, headers = self.swift.calls_with_headers[0]
        self.assertTrue(headers.get('If-Match') is None)
        self.assertTrue(headers.get('If-Modified-Since') is None)

    def test_upload_part_copy_headers_with_not_match(self):
        account = 'test:tester'
        etag = '7dfa07a8e59ddbcd1dc84d4c4f82aea1'
        last_modified_since = 'Fri, 01 Apr 2014 12:00:00 GMT'

        header = {'X-Amz-Copy-Source-If-None-Match': etag,
                  'X-Amz-Copy-Source-If-Unmodified-Since': last_modified_since}
        status, header, body = \
            self._test_copy_for_s3acl(account, put_header=header)

        self.assertEquals(status.split()[0], '200')
        self.assertEquals(len(self.swift.calls_with_headers), 4)
        _, _, headers = self.swift.calls_with_headers[-2]
        self.assertEquals(headers['If-None-Match'], etag)
        self.assertEquals(headers['If-Unmodified-Since'], last_modified_since)
        _, _, headers = self.swift.calls_with_headers[-1]
        self.assertTrue(headers.get('If-None-Match') is None)
        self.assertTrue(headers.get('If-Unmodified-Since') is None)
        _, _, headers = self.swift.calls_with_headers[0]
        self.assertTrue(headers.get('If-None-Match') is None)
        self.assertTrue(headers.get('If-Unmodified-Since') is None)

    @s3acl(s3acl_only=True)
    def test_upload_part_copy_headers_with_not_match_and_s3acl(self):
        account = 'test:tester'
        etag = '7dfa07a8e59ddbcd1dc84d4c4f82aea1'
        last_modified_since = 'Fri, 01 Apr 2014 12:00:00 GMT'

        header = {'X-Amz-Copy-Source-If-None-Match': etag,
                  'X-Amz-Copy-Source-If-Unmodified-Since': last_modified_since}
        status, header, body = \
            self._test_copy_for_s3acl(account, put_header=header)

        self.assertEquals(status.split()[0], '200')
        self.assertEquals(len(self.swift.calls_with_headers), 4)
        # Before the check of the copy source in the case of s3acl is valid,
        # Swift3 check the bucket write permissions and the object existence
        # of the destination.
        _, _, headers = self.swift.calls_with_headers[-3]
        self.assertTrue(headers.get('If-Match') is None)
        self.assertTrue(headers.get('If-Modified-Since') is None)
        _, _, headers = self.swift.calls_with_headers[-2]
        self.assertEquals(headers['If-None-Match'], etag)
        self.assertEquals(headers['If-Unmodified-Since'], last_modified_since)
        self.assertTrue(headers.get('If-Match') is None)
        self.assertTrue(headers.get('If-Modified-Since') is None)
        _, _, headers = self.swift.calls_with_headers[-1]
        self.assertTrue(headers.get('If-None-Match') is None)
        self.assertTrue(headers.get('If-Unmodified-Since') is None)
        _, _, headers = self.swift.calls_with_headers[0]

if __name__ == '__main__':
    unittest.main()
