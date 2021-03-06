################################################################################
# (c) 2006, The Honeynet Project
#   Author: Jed Haile  jed.haile@thelogangroup.biz
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
################################################################################ 

# $Id$

import re
import sys
import socket
import string
from operator import itemgetter 
import dpkt
import pcap

from base import Base
from output import stringFormatMessage
  
class pcapReError(Exception):
    pass
              

def gen_cmpx(server_port_list):
    """
    Generate a closure to sort an arroy of (count, port) values
    If a port appears in server_port_list, it is assumed to be lower in value than a non-member
    """ 
    def cmpx(x, y):   
        if cmp(x[0], y[0]):
            return cmp(y[0], x[0])
        else:
            if x[1] in server_port_list and not y[1] in server_port_list:
                return -1
            if y[1] in server_port_list and not x[1] in server_port_list:
                return 1
            return cmp(x[1], y[1])
    return cmpx

class pcapRE(Base):
    """
    Takes a pcapObj as an argument.
    """
    def __init__(self, pcapObj):
        Base.__init__(self)
        self.exp = None
        self.p = pcapObj    
        self.action = None
        self.doWordSearch = 0                                
        format = "%(pattern)-10s %(proto)-5s %(source)-15s %(sport)-15s %(dest)-15s %(dport)-5s %(count)10s\n"  
        self.msg = stringFormatMessage(format=format)        
        
    def setRE(self, pattern):
        """
        Arg is a string that will be treated as a regular expression
        """
        self.exp = re.compile(pattern)
        self.pattern = pattern

    def setAction(self, action):
        self.action=action

    def setWordSearch(self, searcher):
        """ Takes an instance of class wordSearch as arg"""
        self.doWordSearch = 1
        self.searcher = searcher
        
    def start(self):
        """Iterate over a pcap object"""  
        if not self.action:
            raise pcapReError('Action not set (use setAction)')  
        for ts, buf in self.p:
            self.packetHandler(ts, buf)

    def packetHandler(self, ts, buf):   
        """Process a pcap packet buffer""" 
        pay = None
        m = None
        try:
            pkt = dpkt.ethernet.Ethernet(buf)
            subpkt = pkt.data
            if type(subpkt) != type(dpkt.ip.IP()):
                # skip non IP packets
                return
            proto = subpkt.p
            shost = socket.inet_ntoa(subpkt.src)
            dhost = socket.inet_ntoa(subpkt.dst)
        except dpkt.Error:
            return
        try:
            if proto == socket.IPPROTO_TCP:
                tcp = subpkt.data
                pay = tcp.data
                dport = tcp.dport 
                sport = tcp.sport
            if proto == socket.IPPROTO_UDP:
                udp = subpkt.data
                pay = udp.data
                dport = udp.dport
                sport = udp.sport
        except dpkt.Error:
            return        
        if pay is not None and self.exp is not None:
            m = self.exp.search(pay)
            if m:                   
                self.action(m, proto, shost, sport, dhost, dport, pay)
     
class pcapReCounter(pcapRE):
    """Extension of pcapRE to do simple counting of matching packets"""
    def __init__(self, pcapObj):
        pcapRE.__init__(self, pcapObj) 
        self.results = {}          
        self.action = self.simpleCounter

    def simpleCounter(self, m, proto, shost, sport, dhost, dport, pay):
        """Simple action that just counts matches"""  
        key = (proto, shost, sport, dhost, dport) 
        if key not in self.results:
            self.results[key] = 0
        self.results[key] += 1
        if self.doWordSearch:
            self.searcher.findWords(pay, key)
    
    def writeResults(self):
        """Summarise results for simpleCounter()"""  
        if self.results:     
            self.msg.msg=dict(pattern="PATTERN", proto="PROTO", source="SOURCE", sport="SPORT", dest="DEST", dport="DPORT", count="COUNT")
            self.doOutput(self.msg)
            for key, val in self.results.items():
                self.msg.msg=dict(pattern=self.pattern, proto=key[0], source=key[1], sport=key[2], dest=key[3], dport=key[4], count=val)
                self.doOutput(self.msg)  
        else:
            self.doOutput('No matching packets found\n')
        if self.doWordSearch:  
            self.searcher.writeResults()  
            
    def server_ports(self, server_port_list=[]): 
        """
        Takes as input the results from a pcapRECount object, and works out which ports are the server ports
        If we have two ports with equal counts, assume the lower numbered is the server unless one of the ports
        is in server_port_list
        """   
        ports = {}
        for key, val in self.results.items():  
            proto=key[0]
            source=key[1]
            sport=key[2]
            dest=key[3]
            dport=key[4]
            count=val
            if proto == socket.IPPROTO_TCP:
                if ports.has_key(sport):
                    ports[sport].add(dport)
                else:
                    ports[sport] = set([dport])
                if ports.has_key(dport):
                    ports[dport].add(sport)
                else:
                    ports[dport] = set([sport])
        portcount = []
        for i in ports.keys():
            portcount.append( (len(ports[i]), i) )
        res = {}                    
        seen = {}      
        for port in [ i[1] for i in sorted(portcount, cmp=gen_cmpx(server_port_list) )]:
            if seen.has_key(port):
                continue
            if res.has_key(port):
                res[port].add(port)
            else:
                res[port] = ports[port]
            for subport in ports[port]:
                seen[subport] = True  
        return res.keys() 
     
class wordSearch(Base):
    """
    wordSeach is an auxillary of pcapReCounter. It allows you to pass a list of words 
    you wish to search for to pcapRE.
    """
    def __init__(self):
        Base.__init__(self)
        self.results = {}
        self.words = []
        format = "%(word)-10s %(proto)-5s %(source)-17s %(dest)-17s %(dport)-7s %(count)10s\n"
        self.msg = stringFormatMessage(format=format)

    def findWords(self, data, key):
        for w in self.words:
            if string.find(data, w) >= 0:
                if key is not None:
                    if not self.results.has_key(w):
                        self.results[w] = {}
                    if key not in self.results[w]:
                        self.results[w][key] = 0 
                    self.results[w][key] += 1

    def setWords(self, wordstr):
        self.words = []
        for w in wordstr.split(" "):
            #self.results[w] = {}
            self.words.append(w)

    def writeResults(self):  
        """Summarise results"""
        if self.results:
            #self.doOutput("Word Matches\n")      
            self.msg.msg=dict(word="WORD", proto="PROTO", source="SOURCE", dest="DEST", dport="DPORT", count="COUNT")
            self.doOutput(self.msg)
            for word, cons in self.results.items():
                for k in cons: 
                    self.msg.msg = dict(word=word, proto=k[0], source=k[1], sport=k[2], dest=k[3], dport=k[4], count=self.results[word][k])
                    self.doOutput(self.msg)   
        else:
             self.doOutput("No words found\n")
              