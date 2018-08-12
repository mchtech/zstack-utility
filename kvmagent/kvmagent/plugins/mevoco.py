__author__ = 'frank'

from kvmagent import kvmagent
from kvmagent.plugins import vm_plugin
from zstacklib.utils import jsonobject
from zstacklib.utils import http
from zstacklib.utils import log
from zstacklib.utils import shell
from zstacklib.utils import sizeunit
from zstacklib.utils import linux
from zstacklib.utils import thread
from zstacklib.utils import iptables
from zstacklib.utils import ebtables
from zstacklib.utils import lock
from zstacklib.utils.bash import *
from zstacklib.utils import ip
import os.path
import re
import threading
import time
import email
import cStringIO as c
from email.mime.multipart import MIMEMultipart
from jinja2 import Template

logger = log.get_logger(__name__)
EBTABLES_CMD = ebtables.get_ebtables_cmd()

class ApplyDhcpRsp(kvmagent.AgentResponse):
    pass

class ReleaseDhcpRsp(kvmagent.AgentResponse):
    pass

class PrepareDhcpRsp(kvmagent.AgentResponse):
    pass

class ApplyUserdataRsp(kvmagent.AgentResponse):
    pass

class ReleaseUserdataRsp(kvmagent.AgentResponse):
    pass

class ConnectRsp(kvmagent.AgentResponse):
    pass

class ResetGatewayRsp(kvmagent.AgentResponse):
    pass

class DeleteNamespaceRsp(kvmagent.AgentResponse):
    pass

class SetForwardDnsCmd(kvmagent.AgentCommand):
    def __init__(self):
        super(SetForwardDnsCmd, self).__init__()
        self.dns = None
        self.mac = None
        self.bridgeName = None
        self.nameSpace = None
        self.wrongDns = None

class SetForwardDnsRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(SetForwardDnsRsp, self).__init__()


class RemoveForwardDnsCmd(kvmagent.AgentCommand):
    def __init__(self):
        super(RemoveForwardDnsCmd, self).__init__()
        self.dns = None
        self.mac = None
        self.bridgeName = None
        self.nameSpace = None

class RemoveForwardDnsRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(RemoveForwardDnsRsp, self).__init__()


class UserDataEnv(object):
    def __init__(self, bridge_name, namespace_name):
        self.bridge_name = bridge_name
        self.namespace_name = namespace_name
        self.outer_dev = None
        self.inner_dev = None

    @lock.lock('prepare_dhcp_namespace')
    @lock.file_lock('/run/xtables.lock')
    @in_bash
    def prepare(self):
        NAMESPACE_ID = None

        NAMESPACE_NAME = self.namespace_name
        out = bash_errorout("ip netns list-id | grep -w {{NAMESPACE_NAME}} | awk '{print $2}'").strip(' \t\n\r')
        if not out:
            out = bash_errorout("ip netns list-id | tail -n 1 | awk '{print $2}'").strip(' \t\r\n')
            if not out:
                NAMESPACE_ID = 0
            else:
                NAMESPACE_ID = int(out) + 1
        else:
            NAMESPACE_ID = int(out)

        logger.debug('use id[%s] for the namespace[%s]' % (NAMESPACE_ID, NAMESPACE_NAME))

        BR_NAME = self.bridge_name
        BR_PHY_DEV = self.bridge_name.replace('br_', '', 1).replace('_', '.', 1)
        self.outer_dev = OUTER_DEV = "outer%s" % NAMESPACE_ID
        self.inner_dev = INNER_DEV = "inner%s" % NAMESPACE_ID

        ret = bash_r('ip netns exec {{NAMESPACE_NAME}} ip link show')
        if ret != 0:
            bash_errorout('ip netns add {{NAMESPACE_NAME}}')
            bash_errorout('ip netns set {{NAMESPACE_NAME}} {{NAMESPACE_ID}}')

        # in case the namespace deleted and the orphan outer link leaves in the system,
        # deleting the orphan link and recreate it
        ret = bash_r('ip netns exec {{NAMESPACE_NAME}} ip link | grep -w {{INNER_DEV}} > /dev/null')
        if ret != 0:
            bash_r('ip link del {{OUTER_DEV}} &> /dev/null')

        ret = bash_r('ip link | grep -w {{OUTER_DEV}} > /dev/null')
        if ret != 0:
            bash_errorout('ip link add {{OUTER_DEV}} type veth peer name {{INNER_DEV}}')

        bash_errorout('ip link set {{OUTER_DEV}} up')

        ret = bash_r('brctl show {{BR_NAME}} | grep -w {{OUTER_DEV}} > /dev/null')
        if ret != 0:
            bash_errorout('brctl addif {{BR_NAME}} {{OUTER_DEV}}')

        ret = bash_r('ip netns exec {{NAMESPACE_NAME}} ip link | grep -w {{INNER_DEV}} > /dev/null')
        if ret != 0:
            bash_errorout('ip link set {{INNER_DEV}} netns {{NAMESPACE_NAME}}')

        bash_errorout('ip netns exec {{NAMESPACE_NAME}} ip link set {{INNER_DEV}} up')


class DhcpEnv(object):
    def __init__(self):
        self.bridge_name = None
        self.dhcp_server_ip = None
        self.dhcp_netmask = None
        self.namespace_name = None


    @lock.lock('prepare_dhcp_namespace')
    @lock.file_lock('/run/xtables.lock')
    @in_bash
    def prepare(self):
        NAMESPACE_ID = None

        NAMESPACE_NAME = self.namespace_name
        out = bash_errorout("ip netns list-id | grep -w {{NAMESPACE_NAME}} | awk '{print $2}'").strip(' \t\n\r')
        if not out:
            out = bash_errorout("ip netns list-id | tail -n 1 | awk '{print $2}'").strip(' \t\r\n')
            if not out:
                NAMESPACE_ID = 0
            else:
                NAMESPACE_ID = int(out) + 1
        else:
            NAMESPACE_ID = int(out)

        logger.debug('use id[%s] for the namespace[%s]' % (NAMESPACE_ID, NAMESPACE_NAME))

        BR_NAME = self.bridge_name
        DHCP_IP = self.dhcp_server_ip
        DHCP_NETMASK = self.dhcp_netmask
        BR_PHY_DEV = self.bridge_name.replace('br_', '', 1).replace('_', '.', 1)
        OUTER_DEV = "outer%s" % NAMESPACE_ID
        INNER_DEV = "inner%s" % NAMESPACE_ID
        CHAIN_NAME = "ZSTACK-%s" % DHCP_IP

        ret = bash_r('ip netns exec {{NAMESPACE_NAME}} ip link show')
        if ret != 0:
            bash_errorout('ip netns add {{NAMESPACE_NAME}}')
            bash_errorout('ip netns set {{NAMESPACE_NAME}} {{NAMESPACE_ID}}')

        # in case the namespace deleted and the orphan outer link leaves in the system,
        # deleting the orphan link and recreate it
        ret = bash_r('ip netns exec {{NAMESPACE_NAME}} ip link | grep -w {{INNER_DEV}} > /dev/null')
        if ret != 0:
            bash_r('ip link del {{OUTER_DEV}} &> /dev/null')

        ret = bash_r('ip link | grep -w {{OUTER_DEV}} > /dev/null')
        if ret != 0:
            bash_errorout('ip link add {{OUTER_DEV}} type veth peer name {{INNER_DEV}}')

        bash_errorout('ip link set {{OUTER_DEV}} up')

        ret = bash_r('brctl show {{BR_NAME}} | grep -w {{OUTER_DEV}} > /dev/null')
        if ret != 0:
            bash_errorout('brctl addif {{BR_NAME}} {{OUTER_DEV}}')

        ret = bash_r('ip netns exec {{NAMESPACE_NAME}} ip link | grep -w {{INNER_DEV}} > /dev/null')
        if ret != 0:
            bash_errorout('ip link set {{INNER_DEV}} netns {{NAMESPACE_NAME}}')

        ret = bash_r('ip netns exec {{NAMESPACE_NAME}} ip addr show {{INNER_DEV}} | grep -w {{DHCP_IP}} > /dev/null')
        if ret != 0 and DHCP_IP != None and DHCP_NETMASK != None:
            bash_errorout('ip netns exec {{NAMESPACE_NAME}} ip addr flush dev {{INNER_DEV}}')
            bash_errorout('ip netns exec {{NAMESPACE_NAME}} ip addr add {{DHCP_IP}}/{{DHCP_NETMASK}} dev {{INNER_DEV}}')

        bash_errorout('ip netns exec {{NAMESPACE_NAME}} ip link set {{INNER_DEV}} up')

        if DHCP_IP is None or DHCP_NETMASK is None:
            logger.debug("no dhcp ip[{{DHCP_IP}}] or netmask[{{DHCP_NETMASK}}] for {{INNER_DEV}} in {{NAMESPACE_NAME}}, skip ebtables/iptables config")
            return

        ret = bash_r(EBTABLES_CMD + ' -L {{CHAIN_NAME}} > /dev/null 2>&1')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -N {{CHAIN_NAME}}')

        ret = bash_r(EBTABLES_CMD + ' -L FORWARD | grep -- "-j {{CHAIN_NAME}}" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -I FORWARD -j {{CHAIN_NAME}}')

        ret = bash_r(EBTABLES_CMD + ' -L {{CHAIN_NAME}} | grep -- "-p ARP -o {{BR_PHY_DEV}} --arp-ip-dst {{DHCP_IP}} -j DROP" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -I {{CHAIN_NAME}} -p ARP -o {{BR_PHY_DEV}} --arp-ip-dst {{DHCP_IP}} -j DROP')

        ret = bash_r(EBTABLES_CMD + ' -L {{CHAIN_NAME}} | grep -- "-p ARP -i {{BR_PHY_DEV}} --arp-ip-dst {{DHCP_IP}} -j DROP" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -I {{CHAIN_NAME}} -p ARP -i {{BR_PHY_DEV}} --arp-ip-dst {{DHCP_IP}} -j DROP')

        ret = bash_r(EBTABLES_CMD + ' -L {{CHAIN_NAME}} | grep -- "-p IPv4 -o {{BR_PHY_DEV}} --ip-proto udp --ip-sport 67:68 -j DROP" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -I {{CHAIN_NAME}} -p IPv4 -o {{BR_PHY_DEV}} --ip-proto udp --ip-sport 67:68 -j DROP')

        ret = bash_r(EBTABLES_CMD + ' -L {{CHAIN_NAME}} | grep -- "-p IPv4 -i {{BR_PHY_DEV}} --ip-proto udp --ip-sport 67:68 -j DROP" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -I {{CHAIN_NAME}} -p IPv4 -i {{BR_PHY_DEV}} --ip-proto udp --ip-sport 67:68 -j DROP')

        ret = bash_r("ebtables-save | grep -- '-A {{CHAIN_NAME}} -j RETURN'")
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -A {{CHAIN_NAME}} -j RETURN')

        # Note(WeiW): fix dhcp checksum, see more at #982
        ret = bash_r("iptables-save | grep -- '-p udp -m udp --dport 68 -j CHECKSUM --checksum-fill'")
        if ret != 0:
            bash_errorout('iptables -w -t mangle -A POSTROUTING -p udp -m udp --dport 68 -j CHECKSUM --checksum-fill')

class Mevoco(kvmagent.KvmAgent):
    APPLY_DHCP_PATH = "/flatnetworkprovider/dhcp/apply"
    PREPARE_DHCP_PATH = "/flatnetworkprovider/dhcp/prepare"
    RELEASE_DHCP_PATH = "/flatnetworkprovider/dhcp/release"
    DHCP_CONNECT_PATH = "/flatnetworkprovider/dhcp/connect"
    RESET_DEFAULT_GATEWAY_PATH = "/flatnetworkprovider/dhcp/resetDefaultGateway"
    APPLY_USER_DATA = "/flatnetworkprovider/userdata/apply"
    RELEASE_USER_DATA = "/flatnetworkprovider/userdata/release"
    BATCH_APPLY_USER_DATA = "/flatnetworkprovider/userdata/batchapply"
    DHCP_DELETE_NAMESPACE_PATH = "/flatnetworkprovider/dhcp/deletenamespace"
    CLEANUP_USER_DATA = "/flatnetworkprovider/userdata/cleanup"
    SET_DNS_FORWARD_PATH = '/dns/forward/set'
    REMOVE_DNS_FORWARD_PATH = '/dns/forward/remove'


    DNSMASQ_CONF_FOLDER = "/var/lib/zstack/dnsmasq/"

    USERDATA_ROOT = "/var/lib/zstack/userdata/"

    def __init__(self):
        self.signal_count = 0

    def start(self):
        http_server = kvmagent.get_http_server()

        http_server.register_async_uri(self.DHCP_CONNECT_PATH, self.connect)
        http_server.register_async_uri(self.APPLY_DHCP_PATH, self.apply_dhcp)
        http_server.register_async_uri(self.BATCH_APPLY_USER_DATA, self.batch_apply_userdata)
        http_server.register_async_uri(self.RELEASE_DHCP_PATH, self.release_dhcp)
        http_server.register_async_uri(self.PREPARE_DHCP_PATH, self.prepare_dhcp)
        http_server.register_async_uri(self.APPLY_USER_DATA, self.apply_userdata)
        http_server.register_async_uri(self.RELEASE_USER_DATA, self.release_userdata)
        http_server.register_async_uri(self.RESET_DEFAULT_GATEWAY_PATH, self.reset_default_gateway)
        http_server.register_async_uri(self.DHCP_DELETE_NAMESPACE_PATH, self.delete_dhcp_namespace)
        http_server.register_async_uri(self.CLEANUP_USER_DATA, self.cleanup_userdata)
        http_server.register_async_uri(self.SET_DNS_FORWARD_PATH, self.setup_dns_forward)
        http_server.register_async_uri(self.REMOVE_DNS_FORWARD_PATH, self.remove_dns_forward)

    def stop(self):
        pass

    @lock.lock('dnsmasq')
    @kvmagent.replyerror
    def remove_dns_forward(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = RemoveForwardDnsRsp()

        conf_file_path, dhcp_path, dns_path, option_path, _ = self._make_conf_path(cmd.nameSpace)
        self._remove_dns_forward(cmd.mac, option_path)
        self._restart_dnsmasq(cmd.nameSpace, conf_file_path)

        return jsonobject.dumps(rsp)

    def _remove_dns_forward(self, mac, option_path):
        TAG = mac.replace(':', '')
        OPTION = option_path

        bash_errorout('''\
sed -i '/{{TAG}},/d' {{OPTION}};
sed -i '/^$/d' {{OPTION}};
''')


    @lock.lock('dnsmasq')
    @kvmagent.replyerror
    def setup_dns_forward(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = SetForwardDnsRsp()

        self._apply_dns_forward(cmd)

        return jsonobject.dumps(rsp)

    def _apply_dns_forward(self, cmd):
        conf_file_path, dhcp_path, dns_path, option_path, log_path = self._make_conf_path(cmd.nameSpace)

        TAG = cmd.mac.replace(':', '')
        OPTION = option_path
        DNS = cmd.wrongDns

        for dns in cmd.wrongDns:
            DNS = dns
            bash_errorout('''\
            sed -i '/{{TAG}},option:dns-server,{{DNS}}/d' {{OPTION}};
            sed -i '/^$/d' {{OPTION}};
            ''')

        DNS = cmd.dns
        option_conf = '''\
tag:{{TAG}},option:dns-server,{{DNS}}

'''
        tmpt = Template(option_conf)
        option_conf = tmpt.render({'TAG': TAG, 'DNS': DNS})
        mode = 'a+'
        with open(option_path, mode) as fd:
            fd.write(option_conf)

        self._restart_dnsmasq(cmd.nameSpace, conf_file_path)


    @kvmagent.replyerror
    @in_bash
    def delete_dhcp_namespace(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        dhcp_ip = bash_o("ip netns exec %s ip route | awk '{print $9}'" % cmd.namespaceName)
        dhcp_ip = dhcp_ip.strip(" \t\n\r")

        if dhcp_ip:
            CHAIN_NAME = "ZSTACK-%s" % dhcp_ip

            o = bash_o("ebtables-save | grep {{CHAIN_NAME}} | grep -- -A")
            o = o.strip(" \t\r\n")
            if o:
                cmds = []
                for l in o.split("\n"):
                    cmds.append(EBTABLES_CMD + " %s" % l.replace("-A", "-D"))

                bash_r("\n".join(cmds))

            ret = bash_r("ebtables-save | grep '\-A {{CHAIN_NAME}} -j RETURN'")
            if ret != 0:
                bash_errorout(EBTABLES_CMD + ' -A {{CHAIN_NAME}} -j RETURN')

        bash_errorout("ps aux | grep -v grep | grep -w dnsmasq | grep -w %s | awk '{printf $2}' | xargs -r kill -9" % cmd.namespaceName)
        bash_errorout("ip netns | grep -w %s | grep -v grep | awk '{print $1}' | xargs -r ip netns del %s" % (cmd.namespaceName, cmd.namespaceName))

        return jsonobject.dumps(DeleteNamespaceRsp())

    @kvmagent.replyerror
    def connect(self, req):
        shell.call(EBTABLES_CMD + ' -F')
        shell.call(EBTABLES_CMD + ' -t nat -F')
        return jsonobject.dumps(ConnectRsp())

    @kvmagent.replyerror
    @in_bash
    def cleanup_userdata(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])

        BR_NAME = cmd.bridgeName
        # max length of ebtables chain name is 31
        if (len(BR_NAME) <= 12):
            CHAIN_NAME = "USERDATA-%s-%s" % (BR_NAME, cmd.l3NetworkUuid[0:8])
        else:
            CHAIN_NAME = "USERDATA-%s-%s" % (BR_NAME[len(BR_NAME) - 12: len(BR_NAME)], cmd.l3NetworkUuid[0:8])

        o = bash_o("ebtables-save | grep {{CHAIN_NAME}} | grep -- -A")
        o = o.strip(" \t\r\n")
        if o:
            cmds = []
            for l in o.split("\n"):
                # we don't distinguish if the rule is in filter table or nat table
                # but try both. The wrong table will silently fail
                cmds.append(EBTABLES_CMD + " -t filter %s" % l.replace("-A", "-D"))
                cmds.append(EBTABLES_CMD + " -t nat %s" % l.replace("-A", "-D"))

            cmds.append(EBTABLES_CMD + " -t nat -X %s" % CHAIN_NAME)
            bash_r("\n".join(cmds))

        bash_errorout("ps aux | grep lighttpd | grep {{BR_NAME}} | grep -w userdata | awk '{print $2}' | xargs -r kill -9")

        return jsonobject.dumps(kvmagent.AgentResponse())

    @kvmagent.replyerror
    def batch_apply_userdata(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])

        if cmd.rebuild:
            # kill all lighttped processes which will be restarted later
            shell.call('pkill -9 lighttpd || true')

        namespaces = {}
        for u in cmd.userdata:
            if u.namespaceName not in namespaces:
                namespaces[u.namespaceName] = u
            else:
                if namespaces[u.namespaceName].dhcpServerIp != u.dhcpServerIp:
                    raise Exception('same namespace [%s] but has different dhcpServerIp: %s, %s ' % (
                        u.namespaceName, namespaces[u.namespaceName].dhcpServerIp, u.dhcpServerIp))
                if namespaces[u.namespaceName].bridgeName != u.bridgeName:
                    raise Exception('same namespace [%s] but has different dhcpServerIp: %s, %s ' % (
                    u.namespaceName, namespaces[u.namespaceName].bridgeName, u.bridgeName))
                if namespaces[u.namespaceName].port != u.port:
                    raise Exception('same namespace [%s] but has different dhcpServerIp: %s, %s ' % (
                    u.namespaceName, namespaces[u.namespaceName].port, u.port))

        for n in namespaces.values():
            self._apply_userdata_xtables(n)

        for u in cmd.userdata:
            self._apply_userdata_vmdata(u)

        for n in namespaces.values():
            self._apply_userdata_restart_httpd(n)

        return jsonobject.dumps(kvmagent.AgentResponse())

    @kvmagent.replyerror
    def apply_userdata(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        self._apply_userdata_xtables(cmd.userdata)
        self._apply_userdata_vmdata(cmd.userdata)
        self._apply_userdata_restart_httpd(cmd.userdata)
        return jsonobject.dumps(ApplyUserdataRsp())

    @in_bash
    @lock.file_lock('/run/xtables.lock')
    def _apply_userdata_xtables(self, to):
        p = UserDataEnv(to.bridgeName, to.namespaceName)
        INNER_DEV = None
        DHCP_IP = None
        NS_NAME = to.namespaceName

        if not to.hasattr("dhcpServerIp"):
            p.prepare()
            INNER_DEV = p.inner_dev
        else:
            DHCP_IP = to.dhcpServerIp
            INNER_DEV = bash_errorout(
                "ip netns exec {{NS_NAME}} ip addr | grep -w {{DHCP_IP}} | awk '{print $NF}'").strip(' \t\r\n')
        if not INNER_DEV:
            p.prepare()
            INNER_DEV = p.inner_dev
        if not INNER_DEV:
            raise Exception('cannot find device for the DHCP IP[%s]' % DHCP_IP)

        ret = bash_r('ip netns exec {{NS_NAME}} ip addr | grep 169.254.169.254 > /dev/null')
        if (ret != 0 and INNER_DEV != None):
            bash_errorout('ip netns exec {{NS_NAME}} ip addr add 169.254.169.254 dev {{INNER_DEV}}')

        r, o = bash_ro('ip netns exec {{NS_NAME}} ip r | wc -l')
        if not to.hasattr("dhcpServerIp") and int(o) == 0:
            bash_errorout('ip netns exec {{NS_NAME}} ip r add default dev {{INNER_DEV}}')

        # set ebtables
        BR_NAME = to.bridgeName
        # BR_NAME is "br_%s_%s"
        ETH_NAME = BR_NAME.replace('br_', '', 1).replace('_', '.', 1)
        MAC = bash_errorout("ip netns exec {{NS_NAME}} ip link show {{INNER_DEV}} | grep -w ether | awk '{print $2}'").strip(' \t\r\n')
        CHAIN_NAME="USERDATA-%s" % BR_NAME
        # max length of ebtables chain name is 31
        if (len(BR_NAME) <= 12):
            EBCHAIN_NAME = "USERDATA-%s-%s" % (BR_NAME, to.l3NetworkUuid[0:8])
        else:
            EBCHAIN_NAME = "USERDATA-%s-%s" % (BR_NAME[len(BR_NAME) - 12 : len(BR_NAME)], to.l3NetworkUuid[0:8])

        ret = bash_r(EBTABLES_CMD + ' -t nat -L {{EBCHAIN_NAME}} >/dev/null 2>&1')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -t nat -N {{EBCHAIN_NAME}}')

        if bash_r(EBTABLES_CMD + ' -t nat -L PREROUTING | grep -- "--logical-in {{BR_NAME}} -j {{EBCHAIN_NAME}}"') != 0:
            bash_errorout(EBTABLES_CMD + ' -t nat -I PREROUTING --logical-in {{BR_NAME}} -j {{EBCHAIN_NAME}}')

        # ebtables has a bug that will eliminate 0 in MAC, for example, aa:bb:0c will become aa:bb:c
        cidr = ip.IpAddress(to.vmIp).toCidr(to.netmask)
        RULE = "-p IPv4 --ip-dst 169.254.169.254 --ip-source %s -j dnat --to-dst %s --dnat-target ACCEPT" % (cidr, MAC.replace(":0", ":"))
        ret = bash_r(EBTABLES_CMD + ' -t nat -L {{EBCHAIN_NAME}} | grep -- "{{RULE}}" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -t nat -I {{EBCHAIN_NAME}} {{RULE}}')

        ret = bash_r(EBTABLES_CMD + ' -t nat -L {{EBCHAIN_NAME}} | grep -- "-j RETURN" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -t nat -A {{EBCHAIN_NAME}} -j RETURN')

        ret = bash_r(EBTABLES_CMD + ' -L {{EBCHAIN_NAME}} >/dev/null 2>&1')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -N {{EBCHAIN_NAME}}')

        ret = bash_r(EBTABLES_CMD + ' -L FORWARD | grep -- "-p ARP --arp-ip-dst 169.254.169.254 -j {{EBCHAIN_NAME}}" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -I FORWARD -p ARP --arp-ip-dst 169.254.169.254 -j {{EBCHAIN_NAME}}')

        ret = bash_r(EBTABLES_CMD + ' -L {{EBCHAIN_NAME}} | grep -- "-i {{ETH_NAME}} -j DROP" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -I {{EBCHAIN_NAME}} -i {{ETH_NAME}} -j DROP')

        ret = bash_r(EBTABLES_CMD + ' -L {{EBCHAIN_NAME}} | grep -- "-o {{ETH_NAME}} -j DROP" > /dev/null')
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -I {{EBCHAIN_NAME}} -o {{ETH_NAME}} -j DROP')

        ret = bash_r("ebtables-save | grep '\-A {{EBCHAIN_NAME}} -j RETURN'")
        if ret != 0:
            bash_errorout(EBTABLES_CMD + ' -A {{EBCHAIN_NAME}} -j RETURN')

        self.work_userdata_iptables(CHAIN_NAME, to)

        conf_folder = os.path.join(self.USERDATA_ROOT, to.namespaceName)
        if not os.path.exists(conf_folder):
            shell.call('mkdir -p %s' % conf_folder)

        conf_path = os.path.join(conf_folder, 'lighttpd.conf')
        http_root = os.path.join(conf_folder, 'html')

        conf = '''\
server.document-root = "{{http_root}}"

server.port = {{port}}
server.bind = "169.254.169.254"
dir-listing.activate = "enable"
index-file.names = ( "index.html" )

server.modules += ( "mod_rewrite" )

$HTTP["remoteip"] =~ "^(.*)$" {
    url.rewrite-once = (
        "^/.*/meta-data/(.+)$" => "../%1/meta-data/$1",
        "^/.*/meta-data$" => "../%1/meta-data",
        "^/.*/meta-data/$" => "../%1/meta-data/",
        "^/.*/user-data$" => "../%1/user-data",
        "^/.*/user_data$" => "../%1/user_data",
        "^/.*/meta_data.json$" => "../%1/meta_data.json",
        "^/.*/password$" => "../%1/password",
        "^/.*/$" => "../%1/$1"
    )
    dir-listing.activate = "enable"
}

mimetype.assign = (
  ".html" => "text/html",
  ".txt" => "text/plain",
  ".jpg" => "image/jpeg",
  ".png" => "image/png"
)'''

        tmpt = Template(conf)
        conf = tmpt.render({
            'http_root': http_root,
            'port': to.port
        })

        if not os.path.exists(conf_path):
            with open(conf_path, 'w') as fd:
                fd.write(conf)
        else:
            with open(conf_path, 'r') as fd:
                current_conf = fd.read()

            if current_conf != conf:
                with open(conf_path, 'w') as fd:
                    fd.write(conf)

    @in_bash
    @lock.file_lock('/run/xtables.lock')
    def _apply_userdata_vmdata(self, to):
        def packUserdata(userdataList):
            if len(userdataList) == 1:
                return userdataList[0]

            combined_message = MIMEMultipart()
            for userdata in userdataList:
                userdata = userdata.strip()
                msg = email.message_from_file(c.StringIO(userdata))
                for part in msg.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    combined_message.attach(part)

            return combined_message.__str__()

        conf_folder = os.path.join(self.USERDATA_ROOT, to.namespaceName)
        http_root = os.path.join(conf_folder, 'html')
        meta_data_json = '''\
{
    "uuid": "{{vmInstanceUuid}}"
}'''
        tmpt = Template(meta_data_json)
        conf = tmpt.render({
            'vmInstanceUuid': to.metadata.vmUuid
        })

        root = os.path.join(http_root, to.vmIp)
        meta_root = os.path.join(root, 'meta-data')
        if not os.path.exists(meta_root):
            shell.call('mkdir -p %s' % meta_root)

        index_file_path = os.path.join(meta_root, 'index.html')
        with open(index_file_path, 'w') as fd:
            fd.write('instance-id')
            if to.metadata.vmHostname:
                fd.write('\n')
                fd.write('local-hostname')

        instance_id_file_path = os.path.join(meta_root, 'instance-id')
        with open(instance_id_file_path, 'w') as fd:
            fd.write(to.metadata.vmUuid)

        if to.metadata.vmHostname:
            vm_hostname_file_path = os.path.join(meta_root, 'local-hostname')
            with open(vm_hostname_file_path, 'w') as fd:
                fd.write(to.metadata.vmHostname)

        if to.userdataList:
            userdata_file_path = os.path.join(root, 'user-data')
            with open(userdata_file_path, 'w') as fd:
                fd.write(packUserdata(to.userdataList))

            windows_meta_data_json_path = os.path.join(root, 'meta_data.json')
            with open(windows_meta_data_json_path, 'w') as fd:
                fd.write(conf)

            windows_userdata_file_path = os.path.join(root, 'user_data')
            with open(windows_userdata_file_path, 'w') as fd:
                fd.write(packUserdata(to.userdataList))

            windows_meta_data_password = os.path.join(root, 'password')
            with open(windows_meta_data_password, 'w') as fd:
                fd.write('')

    @in_bash
    @lock.file_lock('/run/xtables.lock')
    def _apply_userdata_restart_httpd(self, to):
        conf_folder = os.path.join(self.USERDATA_ROOT, to.namespaceName)
        conf_path = os.path.join(conf_folder, 'lighttpd.conf')
        pid = linux.find_process_by_cmdline([conf_path])
        if not pid:
            shell.call('ip netns exec %s lighttpd -f %s' % (to.namespaceName, conf_path))

            def check(_):
                pid = linux.find_process_by_cmdline([conf_path])
                return pid is not None

            if not linux.wait_callback_success(check, None, 5):
                raise Exception('lighttpd[conf-file:%s] is not running after being started %s seconds' % (conf_path, 5))

    @lock.file_lock('/run/xtables.lock')
    def work_userdata_iptables(self, CHAIN_NAME, to):
        # DNAT port 80
        PORT = to.port
        PORT_CHAIN_NAME = "UD-PORT-%s" % PORT
        # delete old chains not matching our port
        OLD_CHAIN = bash_errorout("iptables-save | awk '/^:UD-PORT-/{print substr($1,2)}'").strip(' \n\r\t')
        if OLD_CHAIN and OLD_CHAIN != CHAIN_NAME:
            ret = bash_r('iptables-save -t nat | grep -- "-j {{OLD_CHAIN}}"')
            if ret == 0:
                bash_r('iptables -w -t nat -D PREROUTING -j {{OLD_CHAIN}}')

            bash_errorout('iptables -w -t nat -F {{OLD_CHAIN}}')
            bash_errorout('iptables -w -t nat -X {{OLD_CHAIN}}')
        ret = bash_r('iptables-save | grep -w ":{{PORT_CHAIN_NAME}}" > /dev/null')
        if ret != 0:
            self.bash_ignore_exist_for_ipt('iptables -w -t nat -N {{PORT_CHAIN_NAME}}')
        ret = bash_r('iptables -w -t nat -L PREROUTING | grep -- "-j {{PORT_CHAIN_NAME}}"')
        if ret != 0:
            self.bash_ignore_exist_for_ipt('iptables -w -t nat -I PREROUTING -j {{PORT_CHAIN_NAME}}')
        ret = bash_r(
            'iptables-save -t nat | grep -- "{{PORT_CHAIN_NAME}} -d 169.254.169.254/32 -p tcp -j DNAT --to-destination :{{PORT}}"')
        if ret != 0:
            self.bash_ignore_exist_for_ipt(
                'iptables -w -t nat -A {{PORT_CHAIN_NAME}} -d 169.254.169.254/32 -p tcp -j DNAT --to-destination :{{PORT}}')

    @staticmethod
    def bash_ignore_exist_for_ipt(cmd):
        r, o, e = bash_roe(cmd)
        if r == 0:
            return
        elif r == 1 and "iptables: Chain already exists." in e:
            return
        else:
            raise BashError('failed to execute bash[%s], return code: %s, stdout: %s, stderr: %s' % (cmd, r, o, e))

    @kvmagent.replyerror
    def release_userdata(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        html_folder = os.path.join(self.USERDATA_ROOT, cmd.namespaceName, 'html', cmd.vmIp)
        shell.call('rm -rf %s' % html_folder)
        return jsonobject.dumps(ReleaseUserdataRsp())

    def _make_conf_path(self, namespace_name):
        folder = os.path.join(self.DNSMASQ_CONF_FOLDER, namespace_name)
        if not os.path.exists(folder):
            shell.call('mkdir -p %s' % folder)

        # the conf is created at the initializing time
        conf = os.path.join(folder, 'dnsmasq.conf')

        dhcp = os.path.join(folder, 'hosts.dhcp')
        if not os.path.exists(dhcp):
            shell.call('touch %s' % dhcp)

        dns = os.path.join(folder, 'hosts.dns')
        if not os.path.exists(dns):
            shell.call('touch %s' % dns)

        option = os.path.join(folder, 'hosts.option')
        if not os.path.exists(option):
            shell.call('touch %s' % option)

        log = os.path.join(folder, 'dnsmasq.log')
        if not os.path.exists(log):
            shell.call('touch %s' % log)

        return conf, dhcp, dns, option, log

    @lock.lock('prepare_dhcp')
    @kvmagent.replyerror
    def prepare_dhcp(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        p = DhcpEnv()
        p.bridge_name = cmd.bridgeName
        p.dhcp_server_ip = cmd.dhcpServerIp
        p.dhcp_netmask = cmd.dhcpNetmask
        p.namespace_name = cmd.namespaceName
        p.prepare()

        return jsonobject.dumps(PrepareDhcpRsp())

    @lock.lock('dnsmasq')
    @kvmagent.replyerror
    def reset_default_gateway(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])

        if cmd.namespaceNameOfGatewayToRemove and cmd.macOfGatewayToRemove and cmd.gatewayToRemove:
            conf_file_path, _, _, option_path, _ = self._make_conf_path(cmd.namespaceNameOfGatewayToRemove)
            mac_to_remove = cmd.macOfGatewayToRemove.replace(':', '')

            def is_line_to_delete(line):
                return cmd.gatewayToRemove in line and mac_to_remove in line and 'router' in line

            linux.delete_lines_from_file(option_path, is_line_to_delete)
            self._refresh_dnsmasq(cmd.namespaceNameOfGatewayToRemove, conf_file_path)

        if cmd.namespaceNameOfGatewayToAdd and cmd.macOfGatewayToAdd and cmd.gatewayToAdd:
            conf_file_path, _, _, option_path, _ = self._make_conf_path(cmd.namespaceNameOfGatewayToAdd)
            option = 'tag:%s,option:router,%s\n' % (cmd.macOfGatewayToAdd.replace(':', ''), cmd.gatewayToAdd)
            with open(option_path, 'a+') as fd:
                fd.write(option)

            self._refresh_dnsmasq(cmd.namespaceNameOfGatewayToAdd, conf_file_path)

        return jsonobject.dumps(ResetGatewayRsp())

    @lock.lock('dnsmasq')
    @kvmagent.replyerror
    def apply_dhcp(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])

        namespace_dhcp = {}
        for d in cmd.dhcp:
            lst = namespace_dhcp.get(d.namespaceName)
            if not lst:
                lst = []
                namespace_dhcp[d.namespaceName] = lst
            lst.append(d)

        @in_bash
        def apply(dhcp):
            bridge_name = dhcp[0].bridgeName
            namespace_name = dhcp[0].namespaceName
            conf_file_path, dhcp_path, dns_path, option_path, log_path = self._make_conf_path(namespace_name)

            conf_file = '''\
domain-needed
bogus-priv
no-hosts
addn-hosts={{dns}}
dhcp-option=vendor:MSFT,2,1i
dhcp-lease-max=65535
dhcp-hostsfile={{dhcp}}
dhcp-optsfile={{option}}
log-facility={{log}}
interface={{iface_name}}
except-interface=lo
bind-interfaces
leasefile-ro
{% for g in gateways -%}
dhcp-range={{g}},static
{% endfor -%}
'''

            br_num = shell.call("ip netns list-id | grep -w %s | awk '{print $2}'" % namespace_name)
            br_num = br_num.strip(' \t\r\n')
            if not br_num:
                raise Exception('cannot find the ID for the namespace[%s]' % namespace_name)

            tmpt = Template(conf_file)
            conf_file = tmpt.render({
                'dns': dns_path,
                'dhcp': dhcp_path,
                'option': option_path,
                'log': log_path,
                'iface_name': 'inner%s' % br_num,
                'gateways': [d.gateway for d in dhcp if d.gateway]
            })

            restart_dnsmasq = cmd.rebuild
            if not os.path.exists(conf_file_path) or cmd.rebuild:
                with open(conf_file_path, 'w') as fd:
                    fd.write(conf_file)
            else:
                with open(conf_file_path, 'r') as fd:
                    c = fd.read()

                if c != conf_file:
                    logger.debug('dnsmasq configure file for bridge[%s] changed, restart it' % bridge_name)
                    restart_dnsmasq = True
                    with open(conf_file_path, 'w') as fd:
                        fd.write(conf_file)
                    logger.debug('wrote dnsmasq configure file for bridge[%s]\n%s' % (bridge_name, conf_file))


            info = []
            for d in dhcp:
                dhcp_info = {'tag': d.mac.replace(':', '')}
                dhcp_info.update(d.__dict__)
                dhcp_info['dns'] = ','.join(d.dns)
                routes = []
                # add classless-static-route (option 121) for gateway:
                if d.isDefaultL3Network:
                    routes.append(','.join(['0.0.0.0/0', d.gateway]))
                for route in d.hostRoutes:
                    routes.append(','.join([route.prefix, route.nexthop]))
                dhcp_info['routes'] = ','.join(routes)
                info.append(dhcp_info)

                if not cmd.rebuild:
                    self._erase_configurations(d.mac, d.ip, dhcp_path, dns_path, option_path)

            dhcp_conf = '''\
{% for d in dhcp -%}
{% if d.isDefaultL3Network -%}
{{d.mac}},set:{{d.tag}},{{d.ip}},{{d.hostname}},infinite
{% else -%}
{{d.mac}},set:{{d.tag}},{{d.ip}},infinite
{% endif -%}
{% endfor -%}
'''

            tmpt = Template(dhcp_conf)
            dhcp_conf = tmpt.render({'dhcp': info})
            mode = 'a+'
            if cmd.rebuild:
                mode = 'w'

            with open(dhcp_path, mode) as fd:
                fd.write(dhcp_conf)

            option_conf = '''\
{% for o in options -%}
{% if o.isDefaultL3Network -%}
{% if o.gateway -%}
tag:{{o.tag}},option:router,{{o.gateway}}
{% endif -%}
{% if o.dns -%}
tag:{{o.tag}},option:dns-server,{{o.dns}}
{% endif -%}
{% if o.dnsDomain -%}
tag:{{o.tag}},option:domain-name,{{o.dnsDomain}}
{% endif -%}
{% if o.routes -%}
tag:{{o.tag}},option:classless-static-route,{{o.routes}}
{% endif -%}
{% else -%}
tag:{{o.tag}},3
tag:{{o.tag}},6
{% endif -%}
tag:{{o.tag}},option:netmask,{{o.netmask}}
{% if o.mtu -%}
tag:{{o.tag}},option:mtu,{{o.mtu}}
{% endif -%}
{% endfor -%}
    '''
            tmpt = Template(option_conf)
            option_conf = tmpt.render({'options': info})

            with open(option_path, mode) as fd:
                fd.write(option_conf)

            hostname_conf = '''\
{% for h in hostnames -%}
{% if h.isDefaultL3Network and h.hostname -%}
{{h.ip}} {{h.hostname}}
{% endif -%}
{% endfor -%}
    '''
            tmpt = Template(hostname_conf)
            hostname_conf = tmpt.render({'hostnames': info})

            with open(dns_path, mode) as fd:
                fd.write(hostname_conf)

            if restart_dnsmasq:
                self._restart_dnsmasq(namespace_name, conf_file_path)
            else:
                self._refresh_dnsmasq(namespace_name, conf_file_path)

        for k, v in namespace_dhcp.iteritems():
            apply(v)

        rsp = ApplyDhcpRsp()
        return jsonobject.dumps(rsp)

    def _restart_dnsmasq(self, ns_name, conf_file_path):
        pid = linux.find_process_by_cmdline([conf_file_path])
        if pid:
            linux.kill_process(pid)

        NS_NAME = ns_name
        CONF_FILE = conf_file_path
        DNSMASQ = bash_errorout('which dnsmasq').strip(' \t\r\n')
        bash_errorout('ip netns exec {{NS_NAME}} {{DNSMASQ}} --conf-file={{CONF_FILE}} ')

        def check(_):
            pid = linux.find_process_by_cmdline([conf_file_path])
            return pid is not None

        if not linux.wait_callback_success(check, None, 5):
            raise Exception('dnsmasq[conf-file:%s] is not running after being started %s seconds' % (conf_file_path, 5))

    def _refresh_dnsmasq(self, ns_name, conf_file_path):
        pid = linux.find_process_by_cmdline([conf_file_path])
        if not pid:
            self._restart_dnsmasq(ns_name, conf_file_path)
            return

        if self.signal_count > 50:
            self._restart_dnsmasq(ns_name, conf_file_path)
            self.signal_count = 0
            return

        shell.call('kill -1 %s' % pid)
        self.signal_count += 1

    def _erase_configurations(self, mac, ip, dhcp_path, dns_path, option_path):
        MAC = mac
        TAG = mac.replace(':', '')
        DHCP = dhcp_path
        OPTION = option_path
        IP = ip
        DNS = dns_path

        bash_errorout('''\
sed -i '/{{MAC}},/d' {{DHCP}};
sed -i '/,{{IP}},/d' {{DHCP}};
sed -i '/^$/d' {{DHCP}};
sed -i '/{{TAG}},/d' {{OPTION}};
sed -i '/^$/d' {{OPTION}};
sed -i '/^{{IP}} /d' {{DNS}};
sed -i '/^$/d' {{DNS}}
''')


    @lock.lock('dnsmasq')
    @kvmagent.replyerror
    def release_dhcp(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])

        namespace_dhcp = {}
        for d in cmd.dhcp:
            lst = namespace_dhcp.get(d.namespaceName)
            if not lst:
                lst = []
                namespace_dhcp[d.namespaceName] = lst
            lst.append(d)

        @in_bash
        def release(dhcp):
            for d in dhcp:
                conf_file_path, dhcp_path, dns_path, option_path, _ = self._make_conf_path(d.namespaceName)
                self._erase_configurations(d.mac, d.ip, dhcp_path, dns_path, option_path)
                self._restart_dnsmasq(d.namespaceName, conf_file_path)

        for k, v in namespace_dhcp.iteritems():
            release(v)

        rsp = ReleaseDhcpRsp()
        return jsonobject.dumps(rsp)
