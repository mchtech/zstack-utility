from kvmagent import kvmagent
from zstacklib.utils import jsonobject
from zstacklib.utils import http
from zstacklib.utils import lock
from zstacklib.utils import log
from zstacklib.utils.bash import *
from zstacklib.utils import linux
from zstacklib.utils import thread
from jinja2 import Template
import os.path
import re
import time
import traceback
from prometheus_client.core import GaugeMetricFamily,REGISTRY
from prometheus_client import start_http_server

logger = log.get_logger(__name__)

class PrometheusPlugin(kvmagent.KvmAgent):

    COLLECTD_PATH = "/prometheus/collectdexporter/start"

    @kvmagent.replyerror
    @in_bash
    def start_collectd_exporter(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = kvmagent.AgentResponse()

        eths = bash_o("ls /sys/class/net").split()
        interfaces = []
        for eth in eths:
            eth = eth.strip(' \t\n\r')
            if eth == 'lo': continue
            elif eth.startswith('vnic'): continue
            elif eth.startswith('outer'): continue
            elif eth.startswith('br_'): continue
            elif not eth: continue
            else:
                interfaces.append(eth)

        conf_path = os.path.join(os.path.dirname(cmd.binaryPath), 'collectd.conf')

        conf = '''Interval {{INTERVAL}}
FQDNLookup false

LoadPlugin syslog
LoadPlugin aggregation
LoadPlugin cpu
LoadPlugin disk
LoadPlugin interface
LoadPlugin memory
LoadPlugin network
LoadPlugin virt

<Plugin aggregation>
	<Aggregation>
		#Host "unspecified"
		Plugin "cpu"
		#PluginInstance "unspecified"
		Type "cpu"
		#TypeInstance "unspecified"

		GroupBy "Host"
		GroupBy "TypeInstance"

		CalculateNum false
		CalculateSum false
		CalculateAverage true
		CalculateMinimum false
		CalculateMaximum false
		CalculateStddev false
	</Aggregation>
</Plugin>

<Plugin cpu>
  ReportByCpu true
  ReportByState true
  ValuesPercentage true
</Plugin>

<Plugin disk>
  Disk "/^sd[a-z]$/"
  Disk "/^hd[a-z]$/"
  Disk "/^vd[a-z]$/"
  IgnoreSelected false
</Plugin>

<Plugin "interface">
{% for i in INTERFACES -%}
  Interface "{{i}}"
{% endfor -%}
  IgnoreSelected false
</Plugin>

<Plugin memory>
	ValuesAbsolute true
	ValuesPercentage false
</Plugin>

<Plugin virt>
	Connection "qemu:///system"
	RefreshInterval {{INTERVAL}}
	HostnameFormat name
    PluginInstanceFormat name
    BlockDevice "/:hd[a-z]/"
    IgnoreSelected true
</Plugin>

<Plugin network>
	Server "localhost" "25826"
</Plugin>

'''

        tmpt = Template(conf)
        conf = tmpt.render({
            'INTERVAL': cmd.interval,
            'INTERFACES': interfaces,
        })

        need_restart_collectd = False
        if os.path.exists(conf_path):
            with open(conf_path, 'r') as fd:
                old_conf = fd.read()

            if old_conf != conf:
                with open(conf_path, 'w') as fd:
                    fd.write(conf)
                need_restart_collectd = True
        else:
            with open(conf_path, 'w') as fd:
                fd.write(conf)
            need_restart_collectd = True

        cpid = linux.find_process_by_cmdline(['collectd', conf_path])
        mpid = linux.find_process_by_cmdline(['collectdmon', conf_path])

        if not cpid:
            bash_errorout('collectdmon -- -C %s' % conf_path)
        else:
            if need_restart_collectd:
                if not mpid:
                    bash_errorout('kill -TERM %s' % cpid)
                    bash_errorout('collectdmon -- -C %s' % conf_path)
                else:
                    bash_errorout('kill -HUP %s' % mpid)

        pid = linux.find_process_by_cmdline([cmd.binaryPath])
        if not pid:
            EXPORTER_PATH = cmd.binaryPath
            LOG_FILE = os.path.join(os.path.dirname(EXPORTER_PATH), cmd.binaryPath + '.log')
            ARGUMENTS  = cmd.startupArguments
            if not ARGUMENTS:
                ARGUMENTS = ""
            bash_errorout('chmod +x {{EXPORTER_PATH}}')
            bash_errorout("nohup {{EXPORTER_PATH}} {{ARGUMENTS}} >{{LOG_FILE}} 2>&1 < /dev/null &\ndisown")

        self.install_iptables()

        return jsonobject.dumps(rsp)

    @in_bash
    @lock.file_lock('/run/xtables.lock')
    def install_iptables(self):
        def install_iptables_port(port):
            r, o, e = bash_roe("iptables-save | grep -- '-A INPUT -p tcp -m tcp --dport %s'" % port)
            if r == 0:
                rules = o.split('\n')
                for rule in rules:
                    rule = rule.replace("-A ", "-D ")
                    bash_r("iptables %s" % rule)

            bash_r("iptables -w -A INPUT -p tcp --dport %s -j ACCEPT" % port)

        install_iptables_port(7069)
        install_iptables_port(9100)
        install_iptables_port(9103)

    def install_colletor(self):
        class Collector(object):
            def collect(self):
                try:
                    ret = []
                    for c in kvmagent.metric_collectors:
                        ret.extend(c())

                    return ret
                except Exception as e:
                    content = traceback.format_exc()
                    err = '%s\n%s\n' % (str(e), content)
                    logger.warn(err)
                    return []

        REGISTRY.register(Collector())

    def start(self):
        http_server = kvmagent.get_http_server()
        http_server.register_async_uri(self.COLLECTD_PATH, self.start_collectd_exporter)

        self.install_colletor()
        start_http_server(7069)

    def stop(self):
        pass
