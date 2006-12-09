################################################################################
# (c) 2005, The Honeynet Project
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

from util import renameFile
from base import Base
from singletonmixin import HoneysnapSingleton


class smtpDecode(Base):
    def __init__(self):
        Base.__init__(self) 
        hs = HoneysnapSingleton.getInstance()  
        self.options = hs.getOptions()
        self.tf = self.options['time_convert_fn']
        self.statemgr = None
        self.count = 0
        
    def decode(self, state, statemgr):
        self.statemgr = statemgr
        f = state.flow
        if f.dport == 25:
            state.open(flags="rb", statemgr=self.statemgr)
            d = state.fp.readlines()  
            state.close()          
            dlow = [l.lower() for l in d]  
            to = [l.rstrip() for l in dlow if l.find("rcpt to") >= 0]
            subject = [l.rstrip() for l in dlow if l.find("subject") >=0]
            if len(to) == 0:
                return     
            to = set(to)
            realname = "mail-message-%d" % self.count
            self.count +=1 
            fn = renameFile(state, realname)
            # assume the first entry in each list is the correct one 
            if len(subject) == 0:
                self.doOutput("%s sent SMTP to %s, %s at %s\n" % (state.flow.src, state.flow.dst, ",".join(to), self.tf(state.ts)))
            else:
                self.doOutput("%s sent SMTP %s, subject %s at %s\n" % (state.flow.src, state.flow.dst, ",".join(to), " ".join(subject), self.tf(state.ts))) 
            self.doOutput("\tfile: %s\n" % fn)
