import traceback

from kvmagent import kvmagent
from zstacklib.utils import jsonobject
from zstacklib.utils import linux
from zstacklib.utils import log
from zstacklib.utils import shell
from zstacklib.utils import http

logger = log.get_logger(__name__)

class ImageStoreClient(object):

    ZSTORE_PROTOSTR = "zstore://"
    ZSTORE_CLI_PATH = "/usr/local/zstack/imagestore/bin/zstcli -rootca /var/lib/zstack/imagestorebackupstorage/package/certs/ca.pem"
    ZSTORE_DEF_PORT = 8000

    UPLOAD_BIT_PATH = "/imagestore/upload"
    DOWNLOAD_BIT_PATH = "/imagestore/download"
    COMMIT_BIT_PATH = "/imagestore/commit"
    CONVERT_TO_RAW = "/imagestore/convert/raw"

    def _parse_image_reference(self, backupStorageInstallPath):
        if not backupStorageInstallPath.startswith(self.ZSTORE_PROTOSTR):
            raise kvmagent.KvmError('unexpected backup storage install path %s' % backupStorageInstallPath)

        xs = backupStorageInstallPath[len(self.ZSTORE_PROTOSTR):].split('/')
        if len(xs) != 2:
            raise kvmagent.KvmError('unexpected backup storage install path %s' % backupStorageInstallPath)

        return xs[0], xs[1]

    def _build_install_path(self, name, imgid):
        return "{0}{1}/{2}".format(self.ZSTORE_PROTOSTR, name, imgid)

    def upload_image(self, hostname, fpath):
        imf = self.commit_image(fpath)

        cmdstr = '%s -url %s:%s push %s' % (self.ZSTORE_CLI_PATH, hostname, self.ZSTORE_DEF_PORT, fpath)
        logger.debug('pushing %s to image store' % fpath)
        shell.check_run(cmdstr)
        logger.debug('%s pushed to image store' % fpath)

        return imf

    def commit_image(self, fpath):
        cmdstr = '%s -json add -file %s' % (self.ZSTORE_CLI_PATH, fpath)
        logger.debug('adding %s to local image store' % fpath)
        output = shell.call(cmdstr.encode(encoding="utf-8"))
        logger.debug('%s added to local image store' % fpath)

        return jsonobject.loads(output.splitlines()[-1])

    def backup_volume(self, vm, node, bitmap, mode, dest):
        cmdstr = '%s backup -bitmap %s -dest %s -domain %s -drive %s -mode %s' % (self.ZSTORE_CLI_PATH, bitmap, dest, vm, node, mode)
        return shell.call(cmdstr).strip()

    def image_already_pushed(self, hostname, imf):
        cmdstr = '%s -url %s:%s info %s' % (self.ZSTORE_CLI_PATH, hostname, self.ZSTORE_DEF_PORT, self._build_install_path(imf.name, imf.id))
        if shell.run(cmdstr) != 0:
            return False
        return True

    def upload_to_imagestore(self, cmd, req):
        crsp = self.commit_to_imagestore(cmd, req)

        extpara = ""
        taskid = req[http.REQUEST_HEADER].get(http.TASK_UUID)
        if cmd.threadContext:
            if cmd.threadContext['task-stage']:
                extpara += " -stage %s" % cmd.threadContext['task-stage']
            if cmd.threadContext.api:
                taskid = cmd.threadContext.api

        cmdstr = '%s -url %s:%s -callbackurl %s -taskid %s -imageUuid %s %s push %s' % (
            self.ZSTORE_CLI_PATH, cmd.hostname, self.ZSTORE_DEF_PORT, req[http.REQUEST_HEADER].get(http.CALLBACK_URI),
            taskid, cmd.imageUuid, extpara, cmd.primaryStorageInstallPath)
        logger.debug('pushing %s to image store' % cmd.primaryStorageInstallPath)
        shell.call(cmdstr)
        logger.debug('%s pushed to image store' % cmd.primaryStorageInstallPath)

        rsp = kvmagent.AgentResponse()
        rsp.backupStorageInstallPath = jsonobject.loads(crsp).backupStorageInstallPath
        return jsonobject.dumps(rsp)


    def commit_to_imagestore(self, cmd, req):
        fpath = cmd.primaryStorageInstallPath

        # Synchronize cached writes for 'fpath'
        shell.call('/bin/sync ' + fpath)

        # Add the image to registry
        cmdstr = '%s -json  -callbackurl %s -taskid %s -imageUuid %s add -desc \'%s\' -file %s' % (self.ZSTORE_CLI_PATH, req[http.REQUEST_HEADER].get(http.CALLBACK_URI),
                req[http.REQUEST_HEADER].get(http.TASK_UUID), cmd.imageUuid, cmd.description, fpath)
        logger.debug('adding %s to local image store' % fpath)
        output = shell.call(cmdstr.encode(encoding="utf-8"))
        logger.debug('%s added to local image store' % fpath)

        imf = jsonobject.loads(output.splitlines()[-1])

        rsp = kvmagent.AgentResponse()
        rsp.backupStorageInstallPath = self._build_install_path(imf.name, imf.id)
        rsp.size = imf.virtualsize
        rsp.actualSize = imf.size

        return jsonobject.dumps(rsp)

    def download_from_imagestore(self, cachedir, host, backupStorageInstallPath, primaryStorageInstallPath):
        name, imageid = self._parse_image_reference(backupStorageInstallPath)
        if cachedir:
            cmdstr = '%s -url %s:%s -cachedir %s pull -installpath %s %s:%s' % (self.ZSTORE_CLI_PATH, host, self.ZSTORE_DEF_PORT, cachedir, primaryStorageInstallPath, name, imageid)
        else:
            cmdstr = '%s -url %s:%s pull -installpath %s %s:%s' % (self.ZSTORE_CLI_PATH, host, self.ZSTORE_DEF_PORT, primaryStorageInstallPath, name, imageid)

        logger.debug('pulling %s:%s from image store' % (name, imageid))
        shell.call(cmdstr)
        logger.debug('%s:%s pulled to local cache' % (name, imageid))

        return

    def convert_image_raw(self, cmd):
        destPath = cmd.srcPath.replace('.qcow2', '.raw')
        linux.qcow2_convert_to_raw(cmd.srcPath, destPath)
        rsp = kvmagent.AgentResponse()
        rsp.destPath = destPath
        return jsonobject.dumps(rsp)
