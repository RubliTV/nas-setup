<?php
// Private worker bridge. No user-supplied service names, methods, or CLI secrets.
ini_set('display_errors', '0');
try {
    if (posix_geteuid() !== 0 || $argc !== 1) { throw new RuntimeException('denied'); }
    $input = stream_get_contents(STDIN, 1048577);
    if (strlen($input) > 1048576) { throw new RuntimeException('oversized'); }
    $request = json_decode($input, true, 32, JSON_THROW_ON_ERROR);
    $allowed = [
        'disk_list' => ['DiskMgmt', 'enumerateDevices'],
        'disk_wipe' => ['DiskMgmt', 'wipe'],
        'md_list' => ['MdMgmt', 'enumerateDevices'],
        'md_candidates' => ['MdMgmt', 'getCandidates'],
        'md_create' => ['MdMgmt', 'create'],
        'filesystem_list' => ['FileSystemMgmt', 'enumerateFilesystems'],
        'filesystem_create' => ['FileSystemMgmt', 'create'],
        'mount_set' => ['FileSystemMgmt', 'setMountPoint'],
        'share_set' => ['ShareMgmt', 'set'],
        'share_get' => ['ShareMgmt', 'get'],
        'share_path' => ['ShareMgmt', 'getPath'],
        'privileges_set' => ['ShareMgmt', 'setPrivileges'],
        'privileges_get' => ['ShareMgmt', 'getPrivileges'],
        'acl_set' => ['ShareMgmt', 'setFileACL'],
        'acl_get' => ['ShareMgmt', 'getFileACL'],
        'user_set' => ['UserMgmt', 'setUser'],
        'user_get' => ['UserMgmt', 'getUser'],
        'smb_settings_get' => ['SMB', 'getSettings'],
        'smb_settings_set' => ['SMB', 'setSettings'],
        'smb_share_set' => ['SMB', 'setShare'],
        'smb_share_get' => ['SMB', 'getShare'],
        'config_dirty' => ['Config', 'isDirty'],
        'config_apply' => ['Config', 'applyChanges'],
        'exec_output' => ['Exec', 'getOutput']
    ];
    if (array_keys($request) !== ['operation', 'params'] || !isset($allowed[$request['operation']])) {
        throw new RuntimeException('operation');
    }
    require_once('openmediavault/autoloader.inc');
    require_once('openmediavault/globals.inc');
    require_once('openmediavault/functions.inc');
    $context = ['username' => \OMV\Environment::get('OMV_WEBGUI_ADMINUSER_NAME', 'admin'),
                'role' => OMV_ROLE_ADMINISTRATOR];
    if ($request['operation'] === 'config_apply') {
        // Salt deployment can exceed OMV's default 180-second RPC wait while
        // an array is synchronizing. Stay below Python's 1800-second bound.
        \OMV\Environment::set('OMV_ENGINED_SO_RCVTIMEO', 1740);
    }
    if (in_array($request['operation'], ['share_set', 'smb_share_set'], true) &&
        ($request['params']['uuid'] ?? null) === 'new') {
        $request['params']['uuid'] = \OMV\Environment::get('OMV_CONFIGOBJECT_NEW_UUID');
    }
    [$service, $method] = $allowed[$request['operation']];
    $result = \OMV\Rpc\Rpc::call($service, $method, $request['params'], $context, \OMV\Rpc\Rpc::MODE_REMOTE);
    // OMV may return complete user objects; passwords never cross back to job state.
    if ($request['operation'] === 'user_set' && is_array($result)) {
        $result = ['name' => $result['name']];
    }
    echo json_encode(['ok' => true, 'result' => $result], JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    fwrite(STDOUT, '{"ok":false,"error":"omv_rpc_failed"}');
    exit(1);
}
