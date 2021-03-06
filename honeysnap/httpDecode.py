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
import os  
import cStringIO               
import base64
import dpkt
import urllib

from util import findName, renameFile  
from flow import reverse as freverse  
from flowIdentify import flowIdentify 
from flowDecode import flowDecode
import http
        
class httpDecode(flowDecode):
    """General HTTP decoding routines"""
    # method list from dpkt.  Thanks Dug!
    __methods = dict.fromkeys((
        'GET', 'PUT', 'ICY',
        'COPY', 'HEAD', 'LOCK', 'MOVE', 'POLL', 'POST',
        'BCOPY', 'BMOVE', 'MKCOL', 'TRACE', 'LABEL', 'MERGE',
        'DELETE', 'SEARCH', 'UNLOCK', 'REPORT', 'UPDATE', 'NOTIFY',
        'BDELETE', 'CONNECT', 'OPTIONS', 'CHECKIN',
        'PROPFIND', 'CHECKOUT', 'CCM_POST',
        'SUBSCRIBE', 'PROPPATCH', 'BPROPFIND',
        'BPROPPATCH', 'UNCHECKOUT', 'MKACTIVITY',
        'MKWORKSPACE', 'UNSUBSCRIBE', 'RPC_CONNECT',
        'VERSION-CONTROL',
        'BASELINE-CONTROL'
        ))
    __proto = 'HTTP'

    def __init__(self):   
        super(httpDecode, self).__init__()
        self.tf = self.options['time_convert_fn']
        self.statemgr = None
        self.id = flowIdentify()          
        self.served_log = {}
        self.requested_log = {}
        for hp in self.options['honeypots']: 
            self.served_log[hp] = {} 
            self.requested_log[hp] = {}
     
    # next two functions adapted from dsniff    
    def _get_http_user(self, r):
        if 'authorization' in r.headers:
            scheme, auth = r.headers['authorization'].split(None, 1)
            if scheme == 'Basic':
                return base64.decodestring(auth).split(':')[0]
        return '-'    
    
    def _get_log_entry(self, request, response, src, dst, ts):
        """return a dict of header info ready for printing
        r = http.Response, state = honeysnap.flow.state
        """    
        d = { 'method':request.method, 'uri':request.uri, 'ip': src }
        d['user'] = self._get_http_user(request)
        d['host'] = request.headers.get('host', dst)
        for k in ('referer', 'user-agent'):
            d[k] = request.headers.get(k, '-')
        d['ts'] = ts     
        d['status'] = response.__dict__.get('status', '-')
        return d
        
    def _add_log_entry(self, request, response, src, dst, ts):    
        log = self._get_log_entry(request, response, src, dst, ts)
        if log['ip'] in self.options['honeypots']:
             self.requested_log[log['ip']][log['ts']]=log
        else:
             self.served_log[dst][log['ts']]=log
    
    def print_summary(self):            
        """Print summary info"""  
        super(httpDecode, self).print_summary('\nHTTP summary for %s\n\n')   
        if self.options['print_http_logs'] == 'YES': 
            for hp in self.options['honeypots']:                 
                self.doOutput("\nHTTP logfiles for %s\n\n" % hp) 
                if self.requested_log[hp] or self.served_log[hp]:
                    for item in ['requested_log', 'served_log']: 
                        if item == 'served_log' and self.options['print_served'] != 'YES':
                            break
                        a = self.__dict__[item][hp].keys()
                        if a:
                            a.sort()         
                            self.doOutput("\n%s:\n\n" % item)
                            for ts in a: 
                                log = self.__dict__[item][hp][ts]
                                log['ts'] = self.tf(log['ts'])  
                                outstring = repr('%(ip)s - %(user)s [%(ts)s] '
                                    '"%(method)s http://%(host)s%(uri)s" %(status)s - '
                                    '"%(referer)s" "%(user-agent)s"' % log).strip("'") 
                                self.doOutput('%s\n' % outstring)   
                        else:
                            self.doOutput("\n%s: No files seen\n\n" % item)   
                else:
                    self.doOutput('\tNo traffic seen\n\n')
                    
    def determineType(self, data):
        """
        Data should be a list of the data as obtained via file.readlines()
        Attempts to figure out if this data represents a request
        or a response.
        """         
        line = data[0]
        l = line.strip().split()
        # is it a request?
        if len(l) == 3 and l[0] in self.__methods and l[2].startswith(self.__proto):
            return('request', line)

        # is it a response?
        if len(l) >= 2 and l[0].startswith(self.__proto) and l[1].isdigit():
            return('response', line)

        #print "determineType:unknown type, probably binary "
        return None, None

    def check_data(self, data):
        """
        This is a hack until we have proper fragment re-assembly
        Look for methods in stream, and remove any leading junk
        """               
        l = data[0].strip().split()
        if len(l) == 4:                                                          
            if (l[0] not in self.__methods and l[1] in self.__methods) or \
               (l[0] in self.__methods and l[1] in self.__methods and l[0] == l[1]):
                data[0] = ' '.join(l[1:])
                data[0] += '\r\n'
        return data


    def decode(self, state, statemgr):
        """
        Takes an instance of flow.flow_state, and an instance of
        flow.flow_state_manager
        """   
        self.statemgr = statemgr
        state.open(flags="rb", statemgr=self.statemgr)
        d = state.fp.readlines()  
        state.close()
        #print "decode:state ", state.fname
        if len(d) == 0:
            return    
        d = self.check_data(d)
        t, req = self.determineType(d)
        if (t, req) == (None, None):
            # binary data
            return
        d = "".join(d)
        r = None
        f = state.flow
        if t =='response':
            try:
                r = http.Response(d)   
                r.request = req
                if not hasattr(r, "data"):
                    setattr(r,"data", None)
                state.decoded = r
            except (dpkt.Error, ValueError):
                try:  
                    # bad data, try lax parsing
                    state.open(flags="rb", statemgr=self.statemgr)
                    l = state.fp.readline()
                    headers = http.parse_headers(state.fp)
                    r = http.Message()
                    r.headers = headers
                    r.body = state.fp.readlines()
                    r.data = None   
                    r.status = "-"
                    r.request = req
                    state.decoded = r
                    state.close()
                except dpkt.Error:
                    print "response failed to decode: %s " % state.fname
                    pass

        if t == 'request':
            try:
                r = http.Request(d) 
                state.decoded = r
                r.request = req
                if not getattr(r, "data"):
                    r.data = None
            except dpkt.Error:
                try:  
                    # bad data, so let's try some laxer parsing
                    state.open(flags="rb", statemgr=self.statemgr)
                    l = state.fp.readline()
                    headers = http.parse_headers(state.fp)
                    r = http.Message()
                    r.headers = headers
                    r.body = state.fp.readlines()
                    r.request = req
                    r.data = None
                    state.decoded = r
                    state.close()          
                    # frig up some stuff for the logging
                    h = req.split()
                    r.method = h[0].strip()
                    r.uri = h[1].strip()
                except dpkt.Error:
                    print "request failed to decode: %s " % state.fname
                    pass

        if r:
            state.decoded = r
        else:
            return
        if t is not None:
            self.extractHeaders(state, d)
        rs = self.statemgr.find_flow_state(freverse(state.flow)) 
        if not rs:            
            # haven't seen other half - just fake something so that at least the request gets logged.
            if t == 'request':
                dummy_response = http.Response() 
                dummy_response.__dict__['status'] = '-'   
                self._add_log_entry(r, dummy_response, f.src, f.dst, state.ts)                
            return
        if rs.decoded:  
            self._renameFlow(state, t)
        else:                                    
            self.decode(rs, self.statemgr)
        if rs.decoded:
            if t == 'request':
                self._add_log_entry(r, rs.decoded, f.src, f.dst, state.ts) 
            elif t == 'response':
                self._add_log_entry(rs.decoded, r, f.dst, f.src, rs.ts)  


    def _renameFlow(self, state, t):
        """state is a honeysnap.flow.flow_state object, t = response or request"""
        #print "_renameFlow:state", state.fname
        rflow = freverse(state.flow)   
        #print '_renameFlow:rflow   ', rflow
        rs = self.statemgr.find_flow_state(rflow)
        if rs is not None:
            if rs.decoded is not None and state.decoded is not None:
                #print "Both halves decoded"
                user_agent = "UNKNOWN"
                url = 'UNKNOWN'
                r1 = rs.decoded
                if t == 'request':
                    try:           
                        url = urllib.splitquery(state.decoded.uri)[0]
                        realname = url.rsplit("/", 1)[-1] 
                    except AttributeError:
                        realname = 'index.html'
                    try: 
                        url = state.decoded.headers['host'] + url  
                        user_agent = state.decoded.headers['user-agent']
                    except KeyError:
                        pass
                    # reverse flows to get right sense for file renaming    
                    temp = rs
                    rs = state
                    state = temp
                if t == 'response':
                    url = urllib.splitquery(r1.uri)[0]
                    realname = url.rsplit("/", 1)[-1] 
                    try:              
                        user_agent = r1.headers['user-agent']
                        url = r1.headers['host'] + url 
                    except KeyError:
                        # probably something like a CONNECT
                        pass
                if realname == '' or realname == '/' or not realname:
                    realname = 'index.html' 
                fn = renameFile(state, realname)
                id, m5 = self.id.identify(state) 
                outstring = "%s -> %s, %s (%s) at %s\n" % (state.flow.src, state.flow.dst, url, user_agent, self.tf(state.ts))
                outstring = outstring + "\tfile: %s, filetype: %s, md5 sum: %s\n" %(fn,id,m5)
                self.add_flow(state.ts, state.flow.src, state.flow.dst, outstring)

    def extractHeaders(self, state, d):
        """
        Pull the headers and body off the data,
        drop them into the filename.hdr, filename.body files
        Write remaining data back to original file
        Header parsing stolen from dpkt.http
        """
        headers = None
        data = None
        body = None
        request = ""
        f = cStringIO.StringIO(d)
        if state.decoded is not None:
            # this request was successfully decoded
            # so the decoded object will contain all the headers
            # and the detached data
            headers = {}
            headers = state.decoded.headers
            body = state.decoded.body
            try:
                request = state.decoded.request 
            except dpkt.Error:
                request = ""
            try:
                data = state.decoded.data
            except dpkt.Error:
                data = None

        else:
            # dpkt.http failed to decode
            f = cStringIO.StringIO(d)
            headers = {}
            # grab whatever headers we can
            while 1:
                line = f.readline()
                if not line:
                    return
                request = line
                line = line.strip()
                if not line:
                    break
                l = line.split(None, 1)
                if not l[0].endswith(':'):
                    break
                k = l[0][:-1].lower()
                headers[k] = len(l) != 1 and l[1] or ''
            # this state is somehow broken, or dpkt would have decoded it
            # we'll just put the rest of the data into a file
            data = f.readlines()
            data = "".join(data)
            body = None

        # write headers, body, data to files
        if headers is not None and len(headers) > 0: 
            base = state.fname
            base += ".hdr"
            fp = open(base, "wb")
            rf = freverse(state.flow)
            s = "reverse flow: %s\n" % rf.__repr__()
            fp.write(s)
            fp.write(request)
            for k,v in headers.items():
                line = k + " : " + v + "\n"
                fp.write(line)
            fp.close()
        if body is not None and len(body) > 0:   
            base = state.fname
            fp = open(base, "wb")
            if isinstance(body, type([])):
                body = "".join(body)
            fp.write(body)
            fp.close()
        if data is not None and len(data) > 0:  
            base = state.fname
            base += ".data"
            fp = open(base, "wb")
            if isinstance(data, type([])):
                data = "".join(data)
            fp.write(data)
            fp.close()



