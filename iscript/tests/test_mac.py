#!/usr/bin/env python
# coding=utf-8
"""Test iscript.mac
"""
import arrow
import asyncio
from functools import partial
import mock
import os
import pexpect
import pytest
import iscript.mac as mac
from iscript.exceptions import (
    InvalidNotarization, IScriptError, TimeoutError, UnknownAppDir,
)
from scriptworker_client.utils import makedirs


# helpers {{{1
async def noop_async(*args, **kwargs):
    pass


def touch(path):
    parent_dir = os.path.dirname(path)
    makedirs(parent_dir)
    with open(path, 'w') as fh:
        pass


# App {{{1
def test_app():
    """``App`` attributes can be set, and ``check_required_attrs`` raises if
    the required attrs aren't set.

    """
    a = mac.App()
    assert a.orig_path == ''
    a.orig_path = 'foo'
    assert a.orig_path == 'foo'
    a.check_required_attrs(['orig_path'])
    with pytest.raises(IScriptError):
        a.check_required_attrs(['app_path'])


# set_app_path_and_name {{{1
def test_app_path_and_name(mocker):
    """``app_path_and_name`` sets the ``app_path`` and ``app_name`` if not
    already set.

    """

    def fake_get_app_dir(parent_dir):
        x = os.path.basename(parent_dir)
        return os.path.join(parent_dir, '{}.app'.format(x))

    all_paths = [
        mac.App(parent_dir='foo/1'),
        mac.App(parent_dir='foo/2', app_path='foo/2/2.app'),
        mac.App(parent_dir='foo/3', app_path='foo/4/4.app', app_name='4.app'),
    ]
    mocker.patch.object(mac, 'get_app_dir', new=fake_get_app_dir)
    expected = [
        ['foo/1/1.app', '1.app'], ['foo/2/2.app', '2.app'], ['foo/4/4.app', '4.app']
    ]
    for app in all_paths:
        mac.set_app_path_and_name(app)
        assert [app.app_path, app.app_name] == expected.pop(0)


# sign {{{1
@pytest.mark.asyncio
async def test_sign(mocker, tmpdir):
    """Render ``sign`` noop and verify we have complete code coverage.

    """
    key_config = {
        'identity': 'id',
    }
    app = mac.App()
    app.parent_dir = str(tmpdir)
    entitlements_path = os.path.join(tmpdir, 'entitlements')
    app_path = os.path.join(tmpdir, 'foo.app')
    for p in list(mac.INITIAL_FILES_TO_SIGN) + ['Resources/MacOS/foo']:
        p = p.replace('*', 'foo')
        touch(os.path.join(app_path, p))
    mocker.patch.object(mac, 'run_command', new=noop_async)
    await mac.sign(key_config, app, entitlements_path)


# unlock_keychain {{{1
def fake_spawn(val, *args, **kwargs):
    return val


@pytest.mark.parametrize('results', ([1, 0], [0]))
@pytest.mark.asyncio
async def test_unlock_keychain_successful(results, mocker):
    """Mock a successful keychain unlock.

    """

    def fake_expect(*args, **kwargs):
        return results.pop(0)

    child = mocker.Mock()
    child.expect = fake_expect
    child.exitstatus = 0
    child.signalstatus = None
    mocker.patch.object(pexpect, 'spawn', new=partial(fake_spawn, child))
    await mac.unlock_keychain('x', 'y')


@pytest.mark.asyncio
async def test_unlock_keychain_timeout(mocker):
    """``unlock_keychain`` raises a ``TimeoutError`` on pexpect timeout.

    """

    def fake_expect(*args, **kwargs):
        raise pexpect.exceptions.TIMEOUT('foo')

    child = mocker.Mock()
    child.expect = fake_expect
    child.exitstatus = 0
    child.signalstatus = None
    mocker.patch.object(pexpect, 'spawn', new=partial(fake_spawn, child))
    with pytest.raises(TimeoutError):
        await mac.unlock_keychain('x', 'y')


@pytest.mark.asyncio
async def test_unlock_keychain_failure(mocker):
    """``unlock_keychain`` throws an ``IScriptError`` on non-zero exit status.

    """
    results = [0]

    def fake_expect(*args, **kwargs):
        return results.pop(0)

    child = mocker.Mock()
    child.expect = fake_expect
    child.exitstatus = 1
    child.signalstatus = None
    mocker.patch.object(pexpect, 'spawn', new=partial(fake_spawn, child))
    with pytest.raises(IScriptError):
        await mac.unlock_keychain('x', 'y')


# get_app_dir {{{1
@pytest.mark.parametrize('apps, raises', ((
    [], True,
), (
    ['foo.app'], False,
), (
    ['foo.notanapp'], True,
), (
    ['one.app', 'two.app'], True,
)))
def test_get_app_dir(tmpdir, apps, raises):
    """``get_app_dir`` returns the single ``.app`` dir in ``parent_dir``, and
    raises ``UnknownAppDir`` if there is greater or fewer than one ``.app``.

    """
    for app in apps:
        os.makedirs(os.path.join(tmpdir, app))

    if raises:
        with pytest.raises(UnknownAppDir):
            mac.get_app_dir(tmpdir)
    else:
        assert mac.get_app_dir(tmpdir) == os.path.join(tmpdir, apps[0])


# get_key_config {{{1
@pytest.mark.parametrize('key, config_key, raises', ((
    'dep', 'mac_config', False
), (
    'nightly', 'mac_config', False
), (
    'invalid_key', 'mac_config', True
), (
    'dep', 'invalid_config_key', True
)))
def test_get_config_key(key, config_key, raises):
    """``get_config_key`` returns the correct subconfig.

    """
    config = {
        'mac_config': {
            'dep': {
                'key': 'dep',
            },
            'nightly': {
                'key': 'nightly',
            },
        },
    }
    if raises:
        with pytest.raises(IScriptError):
            mac.get_key_config(config, key, config_key=config_key)
    else:
        assert mac.get_key_config(config, key, config_key=config_key) == config[config_key][key]


# get_app_paths {{{1
def test_get_app_paths():
    """``get_app_paths`` creates ``App`` objects with ``orig_path`` set to
    the cot-downloaded artifact paths.

    """
    config = {'work_dir': 'work'}
    task = {
        'payload': {
            'upstreamArtifacts': [{
                'paths': ['public/foo'],
                'taskId': 'task1',
            }, {
                'paths': ['public/bar', 'public/baz'],
                'taskId': 'task2',
            }],
        }
    }
    paths = []
    apps = mac.get_app_paths(config, task)
    for app in apps:
        assert isinstance(app, mac.App)
        paths.append(app.orig_path)
    assert paths == [
        'work/cot/task1/public/foo', 'work/cot/task2/public/bar',
        'work/cot/task2/public/baz'
    ]


# extract_all_apps {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_extract_all_apps(mocker, tmpdir, raises):
    """``extract_all_apps`` creates ``parent_dir`` and raises if any tar command
    fails. The ``run_command`` calls all start with a commandline that calls
    ``tar``.

    """

    async def fake_run_command(*args, **kwargs):
        assert args[0][0] == 'tar'
        if raises:
            raise IScriptError('foo')

    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    work_dir = os.path.join(str(tmpdir), 'work')
    all_paths = [
        mac.App(orig_path=os.path.join(work_dir, 'orig1')),
        mac.App(orig_path=os.path.join(work_dir, 'orig2')),
        mac.App(orig_path=os.path.join(work_dir, 'orig3')),
    ]
    if raises:
        with pytest.raises(IScriptError):
            await mac.extract_all_apps(work_dir, all_paths)
    else:
        await mac.extract_all_apps(work_dir, all_paths)
        for i in ('0', '1', '2'):
            assert os.path.isdir(os.path.join(work_dir, i))


# create_all_app_zipfiles {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_create_all_app_zipfiles(mocker, tmpdir, raises):
    """``create_all_app_zipfiles`` calls ``zip -r``, and raises on failure.

    """

    async def fake_run_command(*args, **kwargs):
        assert args[0][0:2] == ['zip', '-r']
        if raises:
            raise IScriptError('foo')

    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    all_paths = []
    work_dir = str(tmpdir)
    for i in range(3):
        parent_dir = os.path.join(work_dir, str(i))
        app_name = 'fx {}.app'.format(str(i))
        all_paths.append(mac.App(parent_dir=parent_dir, app_name=app_name))

    if raises:
        with pytest.raises(IScriptError):
            await mac.create_all_app_zipfiles(all_paths)
    else:
        await mac.create_all_app_zipfiles(all_paths)


# create_one_app_zipfile {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_create_one_app_zipfile(mocker, tmpdir, raises):
    """``create_one_app_zipfile`` calls the expected cmdline, and raises on
    failure.

    """
    work_dir = str(tmpdir)

    async def fake_run_command(*args, **kwargs):
        assert args[0] == [
            'zip', '-r', os.path.join(work_dir, 'target.zip'),
            '0/0.app', '1/1.app', '2/2.app'
        ]
        if raises:
            raise IScriptError('foo')

    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    all_paths = []
    for i in range(3):
        all_paths.append(mac.App(app_path=os.path.join(work_dir, str(i), '{}.app'.format(i))))
    if raises:
        with pytest.raises(IScriptError):
            await mac.create_one_app_zipfile(work_dir, all_paths)
    else:
        await mac.create_one_app_zipfile(work_dir, all_paths)


# sign_all_apps {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_sign_all_apps(mocker, tmpdir, raises):
    """``sign_all_apps`` calls ``sign`` and raises on failure.

    """
    key_config = {'x': 'y'}
    entitlements_path = 'fake_entitlements_path'
    work_dir = str(tmpdir)
    all_paths = [
        mac.App(parent_dir=os.path.join(work_dir, '0')),
        mac.App(parent_dir=os.path.join(work_dir, '1')),
        mac.App(parent_dir=os.path.join(work_dir, '2')),
    ]

    async def fake_sign(arg1, arg2, arg3):
        assert arg1 == key_config
        assert arg2 in all_paths
        assert arg3 == entitlements_path
        if raises:
            raise IScriptError('foo')

    mocker.patch.object(mac, 'sign', new=fake_sign)
    if raises:
        with pytest.raises(IScriptError):
            await mac.sign_all_apps(key_config, entitlements_path, all_paths)
    else:
        await mac.sign_all_apps(key_config, entitlements_path, all_paths)


# get_bundle_id {{{1
@pytest.mark.parametrize('task_id', (None, 'asdf'))
@pytest.mark.parametrize('counter', (None, 3))
def test_get_bundle_id(mocker, task_id, counter):
    """``get_bundle_id`` returns a unique bundle id

    """
    now = mock.MagicMock()
    now.timestamp = 51
    now.microsecond = 50
    mocker.patch.object(arrow, 'utcnow', return_value=now)
    base = 'org.foo.base'
    expected = base
    if task_id:
        expected = '{}.{}.{}{}'.format(expected, task_id, now.timestamp, now.microsecond)
        mocker.patch.object(os, 'environ', new={'TASK_ID': task_id})
    else:
        expected = '{}.None.{}{}'.format(expected, now.timestamp, now.microsecond)
    if counter:
        expected = '{}.{}'.format(expected, counter)
    assert mac.get_bundle_id(base, counter=counter) == expected


# get_uuid_from_log {{{1
@pytest.mark.parametrize('uuid, raises', ((
    '07307e2c-db26-494c-8630-cfa239d4b86b', False,
), (
    'd4d31c49-c075-4ea1-bb7f-150c74f608e1', False,
), (
    'd4d31c49-c075-4ea1-bb7f-150c74f608e1', 'missing file',
), (
    '%%%%\\\\=', 'missing uuid',
)))
def test_get_uuid_from_log(tmpdir, uuid, raises):
    """``get_uuid_from_log`` returns the correct uuid from the logfile if present.
    It raises if it has problems finding the uuid in the log.

    """
    log_path = os.path.join(str(tmpdir), 'log')
    if raises != 'missing file':
        with open(log_path, 'w') as fh:
            fh.write('foo\nbar\nbaz\n RequestUUID = {} \nblah\n'.format(uuid))
    if raises:
        with pytest.raises(IScriptError):
            mac.get_uuid_from_log(log_path)
    else:
        assert mac.get_uuid_from_log(log_path) == uuid


# get_notarization_status_from_log {{{1
@pytest.mark.parametrize('has_log, status, expected', ((
    True, 'invalid', 'invalid'
), (
    True, 'success', 'success'
), (
    True, 'unknown', None
), (
    False, None, None
)))
def test_get_notarization_status_from_log(tmpdir, has_log, status, expected):
    """``get_notarization_status_from_log`` finds a valid status in the log
    and returns it. If there is a problem or missing/unknown status, it
    returns ``None``.

    """
    log_path = os.path.join(str(tmpdir), 'log')
    if has_log:
        with open(log_path, 'w') as fh:
            fh.write('foo\nbar\nbaz\n Status: {} \nblah\n'.format(status))
    assert mac.get_notarization_status_from_log(log_path) == expected


# wrap_notarization_with_sudo {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_wrap_notarization_with_sudo(mocker, tmpdir, raises):
    """``wrap_notarization_with_sudo`` chunks its requests into one concurrent
    request per each of the ``local_notarization_accounts``. It doesn't log
    the password.

    """
    futures_len = [3, 3, 2]
    pw = 'test_apple_password'

    async def fake_retry_async(_, args, kwargs):
        cmd = args[0]
        end = len(cmd) - 1
        assert cmd[0] == 'sudo'
        log_cmd = kwargs['log_cmd']
        assert cmd[0:end] == log_cmd[0:end]
        assert cmd[end] != log_cmd[end]
        assert cmd[end] == pw
        assert log_cmd[end].replace('*', '') == ''

    async def fake_raise_future_exceptions(futures, **kwargs):
        """``raise_future_exceptions`` mocker."""

        await asyncio.wait(futures)
        assert len(futures) == futures_len.pop(0)
        if raises:
            raise IScriptError('foo')

    def fake_get_uuid_from_log(path):
        return path

    work_dir = str(tmpdir)
    config = {
        'local_notarization_accounts': ['acct0', 'acct1', 'acct2'],
    }
    key_config = {
        'base_bundle_id': 'org.iscript.test',
        'apple_notarization_account': 'test_apple_account',
        'apple_notarization_password': pw,
    }
    all_paths = []
    expected = {}
    # Let's create 8 apps, with 3 sudo accounts, so we expect batches of 3, 3, 2
    for i in range(8):
        parent_dir = os.path.join(work_dir, str(i))
        notarization_log_path = os.path.join(parent_dir, 'notarization.log')
        all_paths.append(mac.App(
            parent_dir=parent_dir,
            zip_path=os.path.join(parent_dir, '{}.zip'.format(i)),
        ))
        expected[notarization_log_path] = notarization_log_path

    mocker.patch.object(mac, 'retry_async', new=fake_retry_async)
    mocker.patch.object(mac, 'raise_future_exceptions', new=fake_raise_future_exceptions)
    mocker.patch.object(mac, 'get_uuid_from_log', new=fake_get_uuid_from_log)
    if raises:
        with pytest.raises(IScriptError):
            await mac.wrap_notarization_with_sudo(config, key_config, all_paths)
    else:
        assert await mac.wrap_notarization_with_sudo(config, key_config, all_paths) == expected


# notarize_no_sudo {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_notarize_no_sudo(mocker, tmpdir, raises):
    """``notarize_no_sudo`` creates a single request to notarize that doesn't
    log the password.

    """
    pw = 'test_apple_password'

    async def fake_retry_async(_, args, kwargs):
        cmd = args[0]
        end = len(cmd) - 1
        assert cmd[0] == 'xcrun'
        log_cmd = kwargs['log_cmd']
        assert cmd[0:end] == log_cmd[0:end]
        assert cmd[end] != log_cmd[end]
        assert cmd[end] == pw
        assert log_cmd[end].replace('*', '') == ''
        if raises:
            raise IScriptError('foo')

    def fake_get_uuid_from_log(path):
        return path

    work_dir = str(tmpdir)
    zip_path = os.path.join(work_dir, 'zip_path')
    log_path = os.path.join(work_dir, 'notarization.log')
    key_config = {
        'base_bundle_id': 'org.iscript.test',
        'apple_notarization_account': 'test_apple_account',
        'apple_notarization_password': pw,
    }
    expected = {log_path: log_path}

    mocker.patch.object(mac, 'retry_async', new=fake_retry_async)
    mocker.patch.object(mac, 'get_uuid_from_log', new=fake_get_uuid_from_log)
    if raises:
        with pytest.raises(IScriptError):
            await mac.notarize_no_sudo(work_dir, key_config, zip_path)
    else:
        assert await mac.notarize_no_sudo(work_dir, key_config, zip_path) == expected


# poll_notarization_uuid {{{1
@pytest.mark.parametrize('statuses, exception', ((
    ['success'], None
), (
    [None, 'success'], None
), (
    [None], IScriptError
), (
    ['invalid'], InvalidNotarization
), (
    [None, None, None, None, None, None, None, None, None], TimeoutError
)))
@pytest.mark.asyncio
async def test_poll_notarization_uuid(mocker, tmpdir, statuses, exception):
    """``poll_notarization_uuid``: returns ``None`` on success; raises a
    ``TimeoutError`` on timeout; raises ``IScriptError`` on failure; and raises
    ``InvalidNotarization`` on ``invalid`` status from Apple. Also, it doesn't
    log passwords.

    """
    pw = 'test_apple_password'

    async def fake_retry_async(_, args, kwargs):
        cmd = args[0]
        end = len(cmd) - 1
        assert cmd[0] == 'xcrun'
        log_cmd = kwargs['log_cmd']
        assert cmd[0:end] == log_cmd[0:end]
        assert cmd[end] != log_cmd[end]
        assert cmd[end] == pw
        assert log_cmd[end].replace('*', '') == ''
        if exception is IScriptError:
            raise IScriptError('foo')

    def fake_get_notarization_status_from_log(path):
        status = statuses.pop(0)
        return status

    mocker.patch.object(mac, 'retry_async', new=fake_retry_async)
    mocker.patch.object(
        mac, 'get_notarization_status_from_log', new=fake_get_notarization_status_from_log
    )
    if exception:
        with pytest.raises(exception):
            await mac.poll_notarization_uuid('uuid', 'user', pw, .5, '/dev/null', sleep_time=.1)
    else:
        assert await mac.poll_notarization_uuid('uuid', 'user', pw, .5, '/dev/null', sleep_time=.1) is None


# poll_all_notarization_status {{{1
@pytest.mark.parametrize('poll_uuids, raises', ((
    {'uuid': 'log_path'}, True,
), (
    {'uuid': 'log_path'}, False,
), (
    {'uuid1': 'log_path1', 'uuid2': 'log_path2'}, False
)))
@pytest.mark.asyncio
async def test_poll_all_notarization_status(mocker, tmpdir, poll_uuids, raises):
    """```poll_all_notarization_status`` concurrently runs a number of
    ``poll_notarization_uuid`` calls, and raises if any of those calls raise.

    """

    async def fake_raise_future_exceptions(futures):
        await asyncio.wait(futures)
        assert len(futures) == len(poll_uuids)
        if raises:
            raise IScriptError('foo')

    key_config = {
        'apple_notarization_account': 'test_apple_account',
        'apple_notarization_password': 'test_apple_password',
        'notarization_poll_timeout': 1,
    }

    mocker.patch.object(mac, 'raise_future_exceptions', new=fake_raise_future_exceptions)
    mocker.patch.object(mac, 'poll_notarization_uuid', new=noop_async)
    if raises:
        with pytest.raises(IScriptError):
            await mac.poll_all_notarization_status(key_config, poll_uuids)

    else:
        assert await mac.poll_all_notarization_status(key_config, poll_uuids) is None


# staple_apps {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_staple_apps(mocker, raises):
    """``staple_apps`` runs stapling concurrently for each ``App``, and raises
    any exceptions hit along the way.

    """

    async def fake_run_command(*args, **kwargs):
        assert args[0][0] == 'xcrun'
        if raises:
            raise IScriptError('foo')

    all_paths = []
    for i in range(3):
        all_paths.append(mac.App(
            parent_dir=str(i),
            app_name='{}.app'.format(i),
        ))
    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    if raises:
        with pytest.raises(IScriptError):
            await mac.staple_apps(all_paths)
    else:
        assert await mac.staple_apps(all_paths) is None


# tar_apps {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_tar_apps(mocker, tmpdir, raises):
    """``tar_apps`` runs tar concurrently for each ``App``, creating the
    app ``target_tar_path``s, and raises any exceptions hit along the way.

    """

    async def fake_raise_future_exceptions(futures):
        await asyncio.wait(futures)
        if raises:
            raise IScriptError('foo')

    work_dir = os.path.join(tmpdir, 'work')
    artifact_dir = os.path.join(tmpdir, 'artifact')
    all_paths = []
    expected = []
    for i in range(3):
        parent_dir = os.path.join(work_dir, str(i))
        app_name = '{}.app'.format(i)
        orig_path = os.path.join(work_dir, 'cot', 'foo', 'public', 'build', str(i), '{}.tar.gz'.format(i))
        # overload pkg_path to track i
        all_paths.append(mac.App(
            parent_dir=parent_dir, app_name=app_name, orig_path=orig_path, pkg_path=str(i)
        ))
        expected.append(os.path.join(artifact_dir, 'public', 'build/{}/{}.tar.gz'.format(i, i)))

    mocker.patch.object(mac, 'run_command', new=noop_async)
    mocker.patch.object(mac, 'raise_future_exceptions', new=fake_raise_future_exceptions)
    if raises:
        with pytest.raises(IScriptError):
            await mac.tar_apps(artifact_dir, all_paths)
    else:
        assert await mac.tar_apps(artifact_dir, all_paths) is None
        assert [x.target_tar_path for x in all_paths] == expected
        for path in expected:
            assert os.path.isdir(os.path.dirname(path))


# create_pkg_files {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_create_pkg_files(mocker, raises):
    """``create_pkg_files`` runs pkgbuild concurrently for each ``App``, and
    raises any exceptions hit along the way.

    """

    async def fake_run_command(cmd, **kwargs):
        assert cmd[0:2] == ['sudo', 'pkgbuild']
        if raises:
            raise IScriptError('foo')

    all_paths = []
    for i in range(3):
        all_paths.append(mac.App(
            app_path='foo/{}/{}.app'.format(i, i),
            parent_dir='foo/{}'.format(i),
        ))
    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    if raises:
        with pytest.raises(IScriptError):
            await mac.create_pkg_files(all_paths)
    else:
        assert await mac.create_pkg_files(all_paths) is None


# sign_pkg_files {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_sign_pkg_files(mocker, raises):
    """``sign_pkg_files`` runs productsign concurrently for each ``App``, and
    raises any exceptions hit along the way.

    """

    async def fake_run_command(cmd, **kwargs):
        assert cmd[0:2] == ['productsign', '--sign']
        if raises:
            raise IScriptError('foo')

    config = {'artifact_dir': 'artifacts'}
    key_config = {'pkg_cert_id': 'cert'}
    all_paths = []
    for i in range(3):
        all_paths.append(mac.App(
            parent_dir='foo/{}'.format(i),
            orig_path='public/{}/{}.tar.gz'.format(i, i),
            app_path='foo/{}/{}.app'.format(i, i),
            app_name='{}.app'.format(i),
        ))
    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    if raises:
        with pytest.raises(IScriptError):
            await mac.sign_pkg_files(config, key_config, all_paths)
    else:
        assert await mac.sign_pkg_files(config, key_config, all_paths) is None


# sign_and_notarize_all {{{1
@pytest.mark.parametrize('notarize_type', ('multi_account', 'single_account', 'single_zip'))
@pytest.mark.asyncio
async def test_sign_and_notarize_all(mocker, tmpdir, notarize_type):
    """Mock ``sign_and_notarize_all`` for full line coverage."""

    artifact_dir = os.path.join(str(tmpdir), 'artifact')
    work_dir = os.path.join(str(tmpdir), 'work')
    config = {
        'artifact_dir': artifact_dir,
        'work_dir': work_dir,
        'local_notarization_accounts': ['acct0', 'acct1', 'acct2'],
        'mac_config': {
            'dep': {
                'notarize_type': notarize_type,
                'signing_keychain': 'keychain_path',
                'base_bundle_id': 'org.test',
                'identity': 'id',
                'keychain_password': 'keychain_password',
                'pkg_cert_id': 'cert_id',
                'apple_notarization_account': 'apple_account',
                'apple_notarization_password': 'apple_password',
                'notarization_poll_timeout': 2,
            },
        },
    }

    task = {
        'payload': {
            'upstreamArtifacts': [{
                'taskId': 'task1',
                'paths': ['public/build/1/target.tar.gz', 'public/build/2/target.tar.gz'],
            }, {
                'taskId': 'task2',
                'paths': ['public/build/3/target.tar.gz'],
            }],
        },
    }

    mocker.patch.object(mac, 'run_command', new=noop_async)
    mocker.patch.object(mac, 'unlock_keychain', new=noop_async)
    mocker.patch.object(mac, 'poll_notarization_uuid', new=noop_async)
    mocker.patch.object(mac, 'get_app_dir', return_value=os.path.join(work_dir, 'foo/bar.app'))
    mocker.patch.object(mac, 'list_files', return_value='foo/bar.app')
    mocker.patch.object(mac, 'get_notarization_status_from_log', return_value=None)
    mocker.patch.object(mac, 'get_uuid_from_log', return_value='uuid')
    await mac.sign_and_notarize_all(config, task)