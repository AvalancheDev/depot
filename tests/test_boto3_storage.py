import os
import uuid

import mock
import requests
from nose import SkipTest

from depot._compat import PY2, unicode_text


S3Storage = None
FILE_CONTENT = b'HELLO WORLD'


class TestS3FileStorage(object):
    def setup(self):
        try:
            global S3Storage
            from depot.io.boto3 import S3Storage
        except ImportError:
            raise SkipTest('Boto not installed')

        env = os.environ
        access_key_id = env.get('AWS_ACCESS_KEY_ID')
        secret_access_key = env.get('AWS_SECRET_ACCESS_KEY')
        if access_key_id is None or secret_access_key is None:
            raise SkipTest('Amazon S3 credentials not available')

        PID = os.getpid()
        NODE = str(uuid.uuid1()).rsplit('-', 1)[-1]  # Travis runs multiple tests concurrently
        self.default_bucket_name = 'filedepot-%s' % (access_key_id.lower(), )
        self.cred = (access_key_id, secret_access_key)
        self.fs = S3Storage(access_key_id, secret_access_key,
                            'filedepot-testfs-%s-%s-%s' % (access_key_id.lower(), NODE, PID))

    def test_fileoutside_depot(self):
        fid = str(uuid.uuid1())
        key = self.fs._bucket_driver.new_key(fid)
        key.put(Body=FILE_CONTENT)

        f = self.fs.get(fid)
        assert f.read() == FILE_CONTENT

    def test_creates_bucket_when_missing(self):
        created_buckets = []
        def mock_make_api_call(_, operation_name, kwarg):
            if operation_name == 'ListBuckets':
                return {'Buckets': []}
            elif operation_name == 'CreateBucket':
                created_buckets.append(kwarg['Bucket'])
                return None
            else:
                assert False, 'Unexpected Call'

        with mock.patch('botocore.client.BaseClient._make_api_call', new=mock_make_api_call):
            S3Storage(*self.cred)
        assert created_buckets == [self.default_bucket_name]

    def test_bucket_failure(self):
        from botocore.exceptions import ClientError
        def mock_make_api_call(_, operation_name, kwarg):
            if operation_name == 'ListBuckets':
                raise ClientError(error_response={'Error': {'Code': 500}},
                                  operation_name=operation_name)

        try:
            with mock.patch('botocore.client.BaseClient._make_api_call', new=mock_make_api_call):
                S3Storage(*self.cred)
        except ClientError:
            pass
        else:
            assert False, 'Should have reraised ClientError'

    def test_client_receives_extra_args(self):
        with mock.patch('boto3.session.Session.resource') as mockresource:
            S3Storage(*self.cred, endpoint_url='http://somehwere.it', region_name='worlwide')
        mockresource.assert_called_once_with('s3', endpoint_url='http://somehwere.it',
                                             region_name='worlwide')

    def test_get_key_failure(self):
        from botocore.exceptions import ClientError

        from botocore.client import BaseClient
        make_api_call = BaseClient._make_api_call
        def mock_make_api_call(cli, operation_name, kwarg):
            if operation_name == 'HeadObject':
                raise ClientError(error_response={'Error': {'Code': 500}},
                                  operation_name=operation_name)
            return make_api_call(cli, operation_name, kwarg)

        fs = S3Storage(*self.cred)
        try:
            with mock.patch('botocore.client.BaseClient._make_api_call', new=mock_make_api_call):
                fs.get(uuid.uuid1())
        except ClientError:
            pass
        else:
            assert False, 'Should have reraised ClientError'

    def test_invalid_modified(self):
        fid = str(uuid.uuid1())
        key = self.fs._bucket_driver.new_key(fid)
        key.put(Body=FILE_CONTENT, Metadata={'x-depot-modified': 'INVALID'})

        f = self.fs.get(fid)
        assert f.last_modified is None, f.last_modified

    def test_public_url(self):
        fid = str(uuid.uuid1())
        key = self.fs._bucket_driver.new_key(fid)
        key.put(Body=FILE_CONTENT)

        f = self.fs.get(fid)
        assert '.s3.amazonaws.com' in f.public_url, f.public_url
        assert f.public_url.endswith('/%s' % fid), f.public_url

    def test_content_disposition(self):
        file_id = self.fs.create(b'content', unicode_text('test.txt'), 'text/plain')
        test_file = self.fs.get(file_id)
        response = requests.get(test_file.public_url)
        assert response.headers['Content-Disposition'] == "inline;filename=\"test.txt\";filename*=utf-8''test.txt"

    def teardown(self):
        for obj in self.fs._bucket_driver.bucket.objects.all():
            obj.delete()

        try:
            self.fs._bucket_driver.bucket.delete()
        except:
            pass