# Authors: Rob Crittenden <rcritten@redhat.com>
#
# Copyright (C) 2013  Red Hat
# see file 'COPYING' for use and warranty information
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import os
import shutil
import tempfile
import time
import pwd

# pylint: disable=import-error
from six.moves.configparser import SafeConfigParser
# pylint: enable=import-error

from ipaplatform.paths import paths
from ipaplatform import services
from ipalib import api, errors
from ipapython import version
from ipapython.ipautil import run, write_tmp_file
from ipapython import admintool
from ipapython.dn import DN
from ipaserver.install.replication import wait_for_task
from ipaserver.install import installutils
from ipapython import ipaldap
from ipaplatform.constants import constants
from ipaplatform.tasks import tasks


ISO8601_DATETIME_FMT = '%Y-%m-%dT%H:%M:%S'

"""
A test gpg can be generated like this:

# cat >keygen <<EOF
     %echo Generating a standard key
     Key-Type: RSA
     Key-Length: 2048
     Name-Real: IPA Backup
     Name-Comment: IPA Backup
     Name-Email: root@example.com
     Expire-Date: 0
     %pubring /root/backup.pub
     %secring /root/backup.sec
     %commit
     %echo done
EOF
# gpg --batch --gen-key keygen
# gpg --no-default-keyring --secret-keyring /root/backup.sec \
      --keyring /root/backup.pub --list-secret-keys
"""


def encrypt_file(filename, keyring, remove_original=True):
    source = filename
    dest = filename + '.gpg'

    args = [paths.GPG,
            '--batch',
            '--default-recipient-self',
            '-o', dest]

    if keyring is not None:
        args.append('--no-default-keyring')
        args.append('--keyring')
        args.append(keyring + '.pub')
        args.append('--secret-keyring')
        args.append(keyring + '.sec')

    args.append('-e')
    args.append(source)

    result = run(args, raiseonerr=False)
    if result.returncode != 0:
        raise admintool.ScriptError('gpg failed: %s' % result.error_log)

    if remove_original:
        os.unlink(source)

    return dest


class Backup(admintool.AdminTool):
    command_name = 'ipa-backup'
    log_file_name = paths.IPABACKUP_LOG

    usage = "%prog [options]"

    description = "Back up IPA files and databases."

    dirs = (paths.IPA_HTML_DIR,
            paths.ROOT_PKI,
            paths.PKI_TOMCAT,
            paths.SYSCONFIG_PKI,
            paths.HTTPD_ALIAS_DIR,
            paths.IPA_RADB_DIR,
            paths.VAR_LIB_PKI_DIR,
            paths.SYSRESTORE,
            paths.IPA_CLIENT_SYSRESTORE,
            paths.IPA_DNSSEC_DIR,
            paths.SSSD_PUBCONF_KRB5_INCLUDE_D_DIR,
            paths.AUTHCONFIG_LAST,
            paths.VAR_LIB_CERTMONGER_DIR,
            paths.VAR_LIB_IPA,
            paths.VAR_RUN_DIRSRV_DIR,
            paths.DIRSRV_LOCK_DIR,
    )

    files = (
        paths.NAMED_CONF,
        paths.NAMED_KEYTAB,
        paths.RESOLV_CONF,
        paths.SYSCONFIG_PKI_TOMCAT,
        paths.SYSCONFIG_DIRSRV,
        paths.SYSCONFIG_NTPD,
        paths.SYSCONFIG_KRB5KDC_DIR,
        paths.SYSCONFIG_IPA_DNSKEYSYNCD,
        paths.SYSCONFIG_IPA_ODS_EXPORTER,
        paths.SYSCONFIG_NAMED,
        paths.SYSCONFIG_ODS,
        paths.ETC_SYSCONFIG_AUTHCONFIG,
        paths.IPA_NSSDB_PWDFILE_TXT,
        paths.IPA_P11_KIT,
        paths.SYSTEMWIDE_IPA_CA_CRT,
        paths.NSSWITCH_CONF,
        paths.KRB5_KEYTAB,
        paths.SSSD_CONF,
        paths.OPENLDAP_LDAP_CONF,
        paths.LIMITS_CONF,
        paths.HTTPD_PASSWORD_CONF,
        paths.HTTP_KEYTAB,
        paths.HTTPD_IPA_KDCPROXY_CONF,
        paths.HTTPD_IPA_PKI_PROXY_CONF,
        paths.HTTPD_IPA_REWRITE_CONF,
        paths.HTTPD_NSS_CONF,
        paths.HTTPD_IPA_CONF,
        paths.SSHD_CONFIG,
        paths.SSH_CONFIG,
        paths.KRB5_CONF,
        paths.IPA_CA_CRT,
        paths.IPA_DEFAULT_CONF,
        paths.DS_KEYTAB,
        paths.NTP_CONF,
        paths.SMB_CONF,
        paths.SAMBA_KEYTAB,
        paths.DOGTAG_ADMIN_P12,
        paths.KRA_AGENT_PEM,
        paths.CACERT_P12,
        paths.KRACERT_P12,
        paths.KRB5KDC_KDC_CONF,
        paths.SYSTEMD_IPA_SERVICE,
        paths.SYSTEMD_SSSD_SERVICE,
        paths.SYSTEMD_CERTMONGER_SERVICE,
        paths.SYSTEMD_PKI_TOMCAT_SERVICE,
        paths.SVC_LIST_FILE,
        paths.OPENDNSSEC_CONF_FILE,
        paths.OPENDNSSEC_KASP_FILE,
        paths.OPENDNSSEC_ZONELIST_FILE,
        paths.OPENDNSSEC_KASP_DB,
        paths.DNSSEC_SOFTHSM2_CONF,
        paths.DNSSEC_SOFTHSM_PIN_SO,
        paths.IPA_ODS_EXPORTER_KEYTAB,
        paths.IPA_DNSKEYSYNCD_KEYTAB,
        paths.HOSTS,
    ) + tuple(
        os.path.join(paths.IPA_NSSDB_DIR, file)
        for file in ('cert8.db', 'key3.db', 'secmod.db')
    )

    logs=(
      paths.VAR_LOG_PKI_DIR,
      paths.VAR_LOG_HTTPD_DIR,
      paths.IPASERVER_INSTALL_LOG,
      paths.KADMIND_LOG,
      paths.MESSAGES,
      paths.IPACLIENT_INSTALL_LOG,
      paths.LOG_SECURE,
      paths.IPASERVER_UNINSTALL_LOG,
      paths.IPACLIENT_UNINSTALL_LOG,
      paths.NAMED_RUN,
    )

    required_dirs=(
      paths.TOMCAT_TOPLEVEL_DIR,
      paths.TOMCAT_CA_DIR,
      paths.TOMCAT_SIGNEDAUDIT_DIR,
      paths.TOMCAT_CA_ARCHIVE_DIR,
      paths.TOMCAT_KRA_DIR,
      paths.TOMCAT_KRA_SIGNEDAUDIT_DIR,
      paths.TOMCAT_KRA_ARCHIVE_DIR,
    )

    def __init__(self, options, args):
        super(Backup, self).__init__(options, args)
        self._conn = None
        self.files = list(self.files)
        self.dirs = list(self.dirs)
        self.logs = list(self.logs)

    @classmethod
    def add_options(cls, parser):
        super(Backup, cls).add_options(parser, debug_option=True)

        parser.add_option("--gpg-keyring", dest="gpg_keyring",
            help="The gpg key name to be used (or full path)")
        parser.add_option("--gpg", dest="gpg", action="store_true",
          default=False, help="Encrypt the backup")
        parser.add_option("--data", dest="data_only", action="store_true",
            default=False, help="Backup only the data")
        parser.add_option("--logs", dest="logs", action="store_true",
            default=False, help="Include log files in backup")
        parser.add_option("--online", dest="online", action="store_true",
            default=False, help="Perform the LDAP backups online, for data only.")


    def setup_logging(self, log_file_mode='a'):
        super(Backup, self).setup_logging(log_file_mode='a')


    def validate_options(self):
        options = self.options
        super(Backup, self).validate_options(needs_root=True)
        installutils.check_server_configuration()

        if options.gpg_keyring is not None:
            if not os.path.exists(options.gpg_keyring + '.pub'):
                raise admintool.ScriptError('No such key %s' %
                    options.gpg_keyring)
            options.gpg = True

        if options.online and not options.data_only:
            self.option_parser.error("You cannot specify --online "
                "without --data")

        if options.gpg:
            tmpfd = write_tmp_file('encryptme')
            newfile = encrypt_file(tmpfd.name, options.gpg_keyring, False)
            os.unlink(newfile)

        if options.data_only and options.logs:
            self.option_parser.error("You cannot specify --data "
                "with --logs")


    def run(self):
        options = self.options
        super(Backup, self).run()

        api.bootstrap(in_server=True, context='backup', confdir=paths.ETC_IPA)
        api.finalize()

        self.log.info("Preparing backup on %s", api.env.host)

        pent = pwd.getpwnam(constants.DS_USER)

        self.top_dir = tempfile.mkdtemp("ipa")
        os.chown(self.top_dir, pent.pw_uid, pent.pw_gid)
        os.chmod(self.top_dir, 0o750)
        self.dir = os.path.join(self.top_dir, "ipa")
        os.mkdir(self.dir)
        os.chmod(self.dir, 0o750)
        os.chown(self.dir, pent.pw_uid, pent.pw_gid)

        self.header = os.path.join(self.top_dir, 'header')

        cwd = os.getcwd()
        try:
            dirsrv = services.knownservices.dirsrv

            self.add_instance_specific_data()

            # We need the dirsrv running to get the list of services
            dirsrv.start(capture_output=False)

            self.get_connection()

            self.create_header(options.data_only)
            if options.data_only:
                if not options.online:
                    self.log.info('Stopping Directory Server')
                    dirsrv.stop(capture_output=False)
            else:
                self.log.info('Stopping IPA services')
                run(['ipactl', 'stop'])

            instance = installutils.realm_to_serverid(api.env.realm)
            if os.path.exists(paths.VAR_LIB_SLAPD_INSTANCE_DIR_TEMPLATE %
                              instance):
                if os.path.exists(paths.SLAPD_INSTANCE_DB_DIR_TEMPLATE %
                                  (instance, 'ipaca')):
                    self.db2ldif(instance, 'ipaca', online=options.online)
                self.db2ldif(instance, 'userRoot', online=options.online)
                self.db2bak(instance, online=options.online)
            if not options.data_only:
                # create backup of auth configuration
                auth_backup_path = os.path.join(paths.VAR_LIB_IPA, 'auth_backup')
                tasks.backup_auth_configuration(auth_backup_path)
                self.file_backup(options)
            self.finalize_backup(options.data_only, options.gpg, options.gpg_keyring)

            if options.data_only:
                if not options.online:
                    self.log.info('Starting Directory Server')
                    dirsrv.start(capture_output=False)
            else:
                self.log.info('Starting IPA service')
                run(['ipactl', 'start'])

        finally:
            try:
                os.chdir(cwd)
            except Exception as e:
                self.log.error('Cannot change directory to %s: %s' % (cwd, e))
            shutil.rmtree(self.top_dir)


    def add_instance_specific_data(self):
        '''
        Add instance-specific files and directories.

        NOTE: this adds some things that may not get backed up.
        '''
        serverid = installutils.realm_to_serverid(api.env.realm)

        for dir in [paths.ETC_DIRSRV_SLAPD_INSTANCE_TEMPLATE % serverid,
                    paths.VAR_LIB_DIRSRV_INSTANCE_SCRIPTS_TEMPLATE % serverid,
                    paths.VAR_LIB_SLAPD_INSTANCE_DIR_TEMPLATE % serverid]:
            if os.path.exists(dir):
                self.dirs.append(dir)

        for file in (
            paths.SYSCONFIG_DIRSRV_INSTANCE % serverid,
            paths.ETC_TMPFILESD_DIRSRV % serverid,
        ):
            if os.path.exists(file):
                self.files.append(file)

        self.logs.append(paths.VAR_LOG_DIRSRV_INSTANCE_TEMPLATE % serverid)


    def get_connection(self):
        '''
        Create an ldapi connection and bind to it using autobind as root.
        '''
        if self._conn is not None:
            return self._conn

        ldap_uri = ipaldap.get_ldap_uri(protocol='ldapi', realm=api.env.realm)
        self._conn = ipaldap.LDAPClient(ldap_uri)

        try:
            self._conn.external_bind()
        except Exception as e:
            self.log.error("Unable to bind to LDAP server %s: %s" %
                (self._conn.host, e))

        return self._conn


    def db2ldif(self, instance, backend, online=True):
        '''
        Create a LDIF backup of the data in this instance.

        If executed online create a task and wait for it to complete.

        For SELinux reasons this writes out to the 389-ds backup location
        and we move it.
        '''
        self.log.info('Backing up %s in %s to LDIF' % (backend, instance))

        cn = time.strftime('export_%Y_%m_%d_%H_%M_%S')
        dn = DN(('cn', cn), ('cn', 'export'), ('cn', 'tasks'), ('cn', 'config'))

        ldifname = '%s-%s.ldif' % (instance, backend)
        ldiffile = os.path.join(
            paths.SLAPD_INSTANCE_LDIF_DIR_TEMPLATE % instance,
            ldifname)

        if online:
            conn = self.get_connection()
            ent = conn.make_entry(
                dn,
                {
                    'objectClass': ['top', 'extensibleObject'],
                    'cn': [cn],
                    'nsInstance': [backend],
                    'nsFilename': [ldiffile],
                    'nsUseOneFile': ['true'],
                    'nsExportReplica': ['true'],
                }
            )

            try:
                conn.add_entry(ent)
            except Exception as e:
                raise admintool.ScriptError('Unable to add LDIF task: %s'
                    % e)

            self.log.info("Waiting for LDIF to finish")
            wait_for_task(conn, dn)
        else:
            args = [paths.DB2LDIF,
                    '-Z', instance,
                    '-r',
                    '-n', backend,
                    '-a', ldiffile]
            result = run(args, raiseonerr=False)
            if result.returncode != 0:
                self.log.critical('db2ldif failed: %s', result.error_log)

        # Move the LDIF backup to our location
        shutil.move(ldiffile, os.path.join(self.dir, ldifname))


    def db2bak(self, instance, online=True):
        '''
        Create a BAK backup of the data and changelog in this instance.

        If executed online create a task and wait for it to complete.
        '''
        self.log.info('Backing up %s' % instance)
        cn = time.strftime('backup_%Y_%m_%d_%H_%M_%S')
        dn = DN(('cn', cn), ('cn', 'backup'), ('cn', 'tasks'), ('cn', 'config'))

        bakdir = os.path.join(paths.SLAPD_INSTANCE_BACKUP_DIR_TEMPLATE % (instance, instance))

        if online:
            conn = self.get_connection()
            ent = conn.make_entry(
                dn,
                {
                    'objectClass': ['top', 'extensibleObject'],
                    'cn': [cn],
                    'nsInstance': ['userRoot'],
                    'nsArchiveDir': [bakdir],
                    'nsDatabaseType': ['ldbm database'],
                }
            )

            try:
                conn.add_entry(ent)
            except Exception as e:
                raise admintool.ScriptError('Unable to to add backup task: %s'
                    % e)

            self.log.info("Waiting for BAK to finish")
            wait_for_task(conn, dn)
        else:
            args = [paths.DB2BAK, bakdir, '-Z', instance]
            result = run(args, raiseonerr=False)
            if result.returncode != 0:
                self.log.critical('db2bak failed: %s', result.error_log)

        shutil.move(bakdir, self.dir)


    def file_backup(self, options):

        def verify_directories(dirs):
            return [s for s in dirs if os.path.exists(s)]

        tarfile = os.path.join(self.dir, 'files.tar')

        self.log.info("Backing up files")
        args = ['tar',
                '--exclude=/var/lib/ipa/backup',
                '--xattrs',
                '--selinux',
                '-cf',
                tarfile
               ]

        args.extend(verify_directories(self.dirs))
        args.extend(verify_directories(self.files))

        if options.logs:
            args.extend(verify_directories(self.logs))

        result = run(args, raiseonerr=False)
        if result.returncode != 0:
            raise admintool.ScriptError('tar returned non-zero code %d: %s' %
                                        (result.returncode, result.error_log))

        # Backup the necessary directory structure. This is a separate
        # call since we are using the '--no-recursion' flag to store
        # the directory structure only, no files.
        missing_directories = verify_directories(self.required_dirs)

        if missing_directories:
            args = ['tar',
                    '--exclude=/var/lib/ipa/backup',
                    '--xattrs',
                    '--selinux',
                    '--no-recursion',
                    '-rf',  # -r appends to an existing archive
                    tarfile,
                   ]
            args.extend(missing_directories)

            result = run(args, raiseonerr=False)
            if result.returncode != 0:
                raise admintool.ScriptError(
                    'tar returned non-zero code %d '
                    'when adding directory structure: %s' %
                    (result.returncode, result.error_log))

        # Compress the archive. This is done separately, since 'tar' cannot
        # append to a compressed archive.
        result = run(['gzip', tarfile], raiseonerr=False)
        if result.returncode != 0:
            raise admintool.ScriptError(
                'gzip returned non-zero code %d '
                'when compressing the backup: %s' %
                (result.returncode, result.error_log))

        # Rename the archive back to files.tar to preserve compatibility
        os.rename(os.path.join(self.dir, 'files.tar.gz'), tarfile)


    def create_header(self, data_only):
        '''
        Create the backup file header that contains the meta data about
        this particular backup.
        '''
        config = SafeConfigParser()
        config.add_section("ipa")
        if data_only:
            config.set('ipa', 'type', 'DATA')
        else:
            config.set('ipa', 'type', 'FULL')
        config.set('ipa', 'time', time.strftime(ISO8601_DATETIME_FMT, time.gmtime()))
        config.set('ipa', 'host', api.env.host)
        config.set('ipa', 'ipa_version', str(version.VERSION))
        config.set('ipa', 'version', '1')

        dn = DN(('cn', api.env.host), ('cn', 'masters'), ('cn', 'ipa'), ('cn', 'etc'), api.env.basedn)
        services_cns = []
        try:
            conn = self.get_connection()
            services = conn.get_entries(dn, conn.SCOPE_ONELEVEL)
        except errors.NetworkError:
            self.log.critical(
              "Unable to obtain list of master services, continuing anyway")
        except Exception as e:
            self.log.error("Failed to read services from '%s': %s" %
                (conn.host, e))
        else:
            services_cns = [s.single_value['cn'] for s in services]

        config.set('ipa', 'services', ','.join(services_cns))
        with open(self.header, 'w') as fd:
            config.write(fd)


    def finalize_backup(self, data_only=False, encrypt=False, keyring=None):
        '''
        Create the final location of the backup files and move the files
        we've backed up there, optionally encrypting them.

        This is done in a couple of steps. We have a directory that
        contains the tarball of the files, a directory that contains
        the db2bak output and an LDIF.

        These, along with the header, are moved into a new subdirectory
        in /var/lib/ipa/backup.
        '''

        if data_only:
            backup_dir = os.path.join(paths.IPA_BACKUP_DIR, time.strftime('ipa-data-%Y-%m-%d-%H-%M-%S'))
            filename = os.path.join(backup_dir, "ipa-data.tar")
        else:
            backup_dir = os.path.join(paths.IPA_BACKUP_DIR, time.strftime('ipa-full-%Y-%m-%d-%H-%M-%S'))
            filename = os.path.join(backup_dir, "ipa-full.tar")

        os.mkdir(backup_dir)
        os.chmod(backup_dir, 0o700)

        os.chdir(self.dir)
        args = ['tar',
                '--xattrs',
                '--selinux',
                '-czf',
                filename,
                '.'
               ]
        result = run(args, raiseonerr=False)
        if result.returncode != 0:
            raise admintool.ScriptError(
                'tar returned non-zero code %s: %s' %
                (result.returncode, result.error_log))

        if encrypt:
            self.log.info('Encrypting %s' % filename)
            filename = encrypt_file(filename, keyring)

        shutil.move(self.header, backup_dir)

        self.log.info('Backed up to %s', backup_dir)
