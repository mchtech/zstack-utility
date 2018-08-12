#!/bin/bash
#Danguous script, which will remove rpms. Please run it in a new minimal isolated system. 
for item in `cat pkg_list`;do rpm -ev $item --nodeps; done
# You need to install a very clean and basic host to download the yum repo.
#rhel6
#yum install -y unzip rabbitmq-server mysql-server mysql java-1.7.0-openjdk libselinux-python python-devel python-setuptools python-pip gcc autoconf iputils tcpdump ethtool haproxy dnsmasq wget curl qemu-img scsi-target-utils httpd openssh-clients openssh-server sshpass sudo bzip2 mysql qemu-kvm bridge-utils libvirt-python libvirt nfs-utils vconfig libvirt-client iptables tar gzip libaio lvm2 lvm2-libs device-mapper-multipath-libs device-mapper-multipath net-tools rsync dnsmasq lighttpd iproute vconfig daemonize --downloadonly --downloaddir=repo
#rhel7
#need to remove libaio, lvm2, device-mapper-event etc packages.
yum install --downloadonly --downloaddir=repo -y unzip rabbitmq-server mysql-server mysql java-1.7.0-openjdk libselinux-python python-devel python-setuptools python-pip gcc autoconf iputils tcpdump ethtool haproxy dnsmasq wget curl qemu-img scsi-target-utils httpd openssh-clients openssh-server sshpass sudo bzip2 mysql qemu-kvm bridge-utils libvirt-python libvirt nfs-utils vconfig libvirt-client iptables tar gzip iptables-services mariadb mariadb-server libaio lvm2 lvm2-libs device-mapper-multipath-libs device-mapper-multipath device-mapper-event device-mapper-event-libs net-tools rsync dnsmasq lighttpd iproute vconfig collectd collectd-disk collectd-virt collectd-write_http libcollectdclient daemonize
