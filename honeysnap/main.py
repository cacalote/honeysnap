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

import sys
import socket
from optparse import OptionParser, Option
import re
import string
import gzip
import os
from fnmatch import fnmatch
from ConfigParser import SafeConfigParser
import tempfile
import dpkt
import pcap

# all the honeysnap imports
# eventually all these will become UDAF modules
# and you will get them all by importing DA
import httpDecode
import ftpDecode
import smtpDecode
import tcpflow
from hsIRC import HoneySnapIRC
from ircDecode import ircDecode
from util import ipnum
from singletonmixin import HoneysnapSingleton
from pcapinfo import pcapInfo
from packetSummary import Summarize
from base import Base
from output import outputSTDOUT, rawPathOutput
from packetCounter import Counter
from pcapRE import pcapRE, wordSearch
from sebekDecode import sebekDecode

FILTERS = {'Counting outbound IP packets:':'src host %s', 
            'Counting outbound FTP packets:':'src host %s and dst port 21',
            'Counting outbound SSH packets:':'src host %s and dst port 22',
            'Counting outbound Telnet packets:':'src host %s and dst port 23',
            'Counting outbound SMTP packets:':'src host %s and dst port 25',
            'Counting outbound HTTP packets:':'src host %s and dst port 80',
            'Counting outbound HTTPS packets:':'src host %s and dst port 443',
            'Counting outbound Sebek packets:':'src host %s and udp port 1101',
            'Counting outbound IRC packets:':'src host %s and dst port 6667'}

class MyOption(Option):
    """
    A class that extends option to allow us to have comma delimited command line args.
    Taken from the documentation for optparse.
    """
    ACTIONS = Option.ACTIONS + ("extend",)
    STORE_ACTIONS = Option.STORE_ACTIONS + ("extend",)
    TYPED_ACTIONS = Option.TYPED_ACTIONS + ("extend",)

    def take_action(self, action, dest, opt, value, values, parser):
        if action == "extend":
            lvalue = value.split(",")
            values.ensure_value(dest, []).extend(lvalue)
        else:
            Option.take_action(
                self, action, dest, opt, value, values, parser)
            
            
def processFile(honeypots, file, dbargs=None):
        """
        Process a pcap file.
        honeypots is a list of honeypot ip addresses
        file is the pcap file to parse
        This function will run any enabled options for each pcap file
        """
        hs = HoneysnapSingleton.getInstance()
        options = hs.getOptions()
        try:
            # This sucks. pcapy wants a path to a file, not a file obj
            # so we have to uncompress the gzipped data into 
            # a tmp file, and pass the path of that file to pcapy
            tmph, tmpf = tempfile.mkstemp()
            tmph = open(tmpf, 'wb')
            gfile = gzip.open(file)
            tmph.writelines(gfile.readlines())
            gfile.close()
            del gfile
            tmph.close()
            deletetmp = 1
        except IOError:
            # got an error, must not be gzipped
            # should probably do a better check here
            tmpf = file
            deletetmp = 0
        options["tmpf"] = tmpf
        try:
            if options["filename"] is not None:
                out = rawPathOutput(options["filename"], mode="a+")
            else:
                out = outputSTDOUT()
        except IOError:
            # we have some error opening the file
            # first we check if the output dir exists
            if not os.path.exists(options["output_data_directory"]):
                # the directory isn't there
                try:
                    os.mkdir(options["output_data_directory"])
                    # now we can create the output file
                    outfile = sys.stdout                    
                    #outfile = open(options["output_data_directory"] + "/results", 'a+')
                except OSError:
                    print "Error creating output directory"
                    sys.exit(1)
            else:
                # there is something at that path. Is it a directory?
                if not os.path.isdir(options["output_data_directory"]):
                    print "Error: output_data_directory exists, but is not a directory."
                else:
                    print "Unknown Error creating output file"
                sys.exit(1)

            
        if options["do_pcap"] == "YES":
            out("\n\nResults for file: %s\n" % file)
            out("Pcap file information:\n")
            pi = pcapInfo(tmpf)
            pi.setOutput(out)
            pi.getStats()
            
            #outfile.write(pi.getStats())
        
        if options["do_packets"] == "YES":
            out("Counting outbound IP packets:\n")
            out("%-40s %10s\n" % ("Filter", "Packets"))
            #outfile.flush()
            for key, filt in FILTERS.items():
                out(key+"\n")
                for ipaddr in honeypots:
                    p = pcap.pcap(tmpf)
                    c = Counter(p)
                    c.setOutput(out)
                    #c.setOutput(options["output_data_directory"] + "/results")
                    f = filt % ipaddr
                    c.setFilter(f)
                    c.count()
                    #count = c.getCount()
                    #c.writeResults()
                out("\n")

        if options["summarize_incoming"] == "YES":
            out("\nINCOMING CONNECTIONS\n")
            p = pcap.pcap(tmpf)
            if dbargs:
                db = dbConnection(dbargs)
            else:
                db = None
            s = Summarize(p, db)
            filt = 'dst host ' + string.join(honeypots, ' or dst host ')
            s.setFilter(filt, file)
            s.setOutput(out)
            #s.setOutput(options["output_data_directory"] + "/results")
            s.start()
            s.writeResults(limit=10)
            del p


        if options["summarize_outgoing"] == "YES":
            out("\nOUTGOING CONNECTIONS\n")
            p = pcap.pcap(tmpf)
            s = Summarize(p, None)
            filt = 'src host ' + string.join(honeypots, ' or src host ')
            s.setFilter(filt, file)
            s.setOutput(out)
            #s.setOutput(options["output_data_directory"] + "/results")
            s.start()
            s.writeResults(limit=10)
            del p


        if options["do_irc_summary"] == "YES" or options["do_irc"] == "YES":
            """
            Here we will use PcapRE to find packets on port 6667 with "PRIVMSG"
            in the payload.  Matching packets will be handed to wordsearch 
            to hunt for any matching words.
            """
            out("\nIRC SUMMARY\n")
            p = pcap.pcap(tmpf)
            words = '0day access account admin auth bank bash #!/bin binaries binary bot card cash cc cent connect crack credit dns dollar ebay e-bay egg flood ftp hackexploit http leech login money /msg nologin owns ownz password paypal phish pirate pound probe prv putty remote resolved root rooted scam scan shell smtp sploit sterling sucess sysop sys-op trade uid uname uptime userid virus warez' 
            if options["wordfile"]:
                wfile = options["wordfile"]
                if os.path.exists(wfile) and os.path.isfile(wfile):
                    wfp = open(wfile, 'rb')
                    words = wfp.readlines()
                    words = [w.strip() for w in words]
                    words = " ".join(words)
            ws = wordSearch()
            ws.setWords(words)
            ws.setOutput(out)
            #ws.setOutput(options["output_data_directory"] + "/results")
            r = pcapRE(p)
            r.setFilter("port 6667")
            r.setRE('PRIVMSG')
            r.setWordSearch(ws)
            r.setOutput(out)
            #r.setOutput(options["output_data_directory"] + "/results")
            r.start()
            r.writeResults()
            del p

        if options["do_irc_detail"] == "YES" or options["do_irc"] == "YES":
            out("\nExtracting from IRC\n")
            p = pcap.pcap(tmpf)
            de = tcpflow.tcpFlow(p)
            de.setFilter("port 6667")
            de.setOutput(out)
            de.setOutdir(options["output_data_directory"]+ "/irc-extract")
            de.start()
            de.dump_extract(options)
            hirc = HoneySnapIRC()
            hirc.connect(tmpf)
            hd = ircDecode()
            hd.setOutput(out)
            hirc.addHandler("all_events", hd.decodeCB, -1)
            hirc.ircobj.process_once()
            hd.printSummary()
            del p
            
        if options["do_http"] == "YES":
            out("\nExtracting from http\n")
            p = pcap.pcap(tmpf)
            de = tcpflow.tcpFlow(p)
            de.setFilter("port 80")
            de.setOutdir(options["output_data_directory"]+ "/http-extract")
            de.setOutput(out)
            decode = httpDecode.httpDecode()
            decode.setOutput(out)
            de.registerPlugin(decode.decode)
            de.start()
            de.dump_extract(options)
            del p
        

        if options["do_ftp"] == "YES":
            out("\nExtracting from ftp\n")
            p = pcap.pcap(tmpf)
            de = tcpflow.tcpFlow(p)
            de.setFilter("port 20 or port 21")
            de.setOutdir(options["output_data_directory"] + "/ftp-extract")
            de.setOutput(out)
            decode = ftpDecode.ftpDecode()
            decode.setOutput(out)
            de.registerPlugin(decode.decode)
            de.start()
            de.dump_extract(options)
            del p

        if options["do_smtp"] == "YES":
            out("\nExtracting from smtp\n")
            p = pcap.pcap(tmpf)
            de = tcpflow.tcpFlow(p)
            de.setFilter("port 25")
            de.setOutdir(options["output_data_directory"] + "/smtp-extract")
            de.setOutput(out)
            decode = smtpDecode.smtpDecode()
            decode.setOutput(out)
            de.registerPlugin(decode.decode)
            de.start()
            de.dump_extract(options)
            del p

##        if options["do_rrd"] == "YES":
##            print "RRD not currently supported"
        
        if options["do_sebek"] == "YES":
            sbd = sebekDecode()
            sbd.setOutput(out)
            sbd.run()
            

        # delete the tmp file we used to hold unzipped data        
        if deletetmp:
            os.unlink(tmpf)

def cleanup(options):
    """
    Clean up empty files, etc.
    """
    datadir = options["output_data_directory"]
    for dir in ["/irc-extract", "/http-extract", "/ftp-extract", "/smtp-extract"]:
        if os.path.isdir(datadir+dir):
            files = os.listdir(datadir+dir)
        else:
            continue
        for f in files:
            file = datadir + dir + "/" + f
            if os.stat(file).st_size == 0:
                os.unlink(file)

def configOptions(parser):
    parser.add_option("-c", "--config", dest="config",type="string",
                  help="Config file")
    parser.add_option("-f", "--file", dest="filename",type="string",
                  help="Write report to FILE", metavar="FILE")
    parser.set_defaults(filename=None)
    parser.add_option("-o", "--output", dest="outputdir",type="string",
                  help="Write output to DIR, defaults to /tmp/analysis", metavar="DIR")
    parser.set_defaults(outputdir="/tmp/analysis")
    parser.add_option("-t", "--tmpdir", dest="tmpdir",type="string",
                  help="Directory to use as a temporary directory, defaults to /tmp")
    parser.set_defaults(tmpdir="/tmp")
    parser.add_option("-H", "--honeypots", dest="honeypots", action="extend", type="string",
                  help="Comma delimited list of honeypots")
##    parser.add_option("-r", "--read", dest="files", type="string",
##                  help="Pcap file to be read. If this flag is set then honeysnap will not run in batch mode. Will also read from stdin.", metavar="FILE")
    parser.add_option("-d", "--dir", dest="files", type="string",
                  help="Directory containing timestamped log directories. If this flag is set then honeysnap will run in batch mode. To limit which directories to parse, use -m (--mask)", metavar="FILE")

    parser.add_option("-m", "--mask", dest="mask", type="string",
            help = "Mask to limit which directories are recursed into.")
    parser.set_defaults(mask="*")

    parser.add_option("-w", "--words", dest="wordfile", type="string",
            help = "Pull wordlist from FILE", metavar="FILE")

    # summary options
    parser.add_option("--do-pcap", dest="do_pcap", action="store_const", const="YES",
            help = "Summarise pcap info")
    parser.set_defaults(do_pcap="YES")
    parser.add_option("--do-packets", dest="do_packets", action="store_const", const="YES",
            help = "Summarise packet counts")
    parser.set_defaults(do_packets="NO")
    parser.add_option("--do-incoming", dest="summarize_incoming", action="store_const", const="YES",
            help = "Summarise incoming traffic flows")
    parser.set_defaults(summarize_incoming="NO")
    parser.add_option("--do-outgoing", dest="summarize_outgoing", action="store_const", const="YES",
            help = "Summarise packet counts")
    parser.set_defaults(summarize_outgoing="NO")

    # protocol options
##    parser.add_option("--do-telnet", dest="do_telnet", action="store_const", const="YES",
##            help = "Count outbound telnet")
##    parser.set_defaults(do_telnet="NO")
##    parser.add_option("--do-ssh", dest="do_ssh", action="store_const", const="YES",
##            help = "Count outbound ssh")
##    parser.set_defaults(do_ssh="NO")
    parser.add_option("--do-http", dest="do_http", action="store_const", const="YES",
            help = "Extract http data")
    parser.set_defaults(do_http="NO")
##    parser.add_option("--do-https", dest="do_https", action="store_const", const="YES",
##            help = "Count outbound https")
##    parser.set_defaults(do_https="NO")
    parser.add_option("--do-ftp", dest="do_ftp", action="store_const", const="YES",
            help = "Extract FTP data")
    parser.set_defaults(do_ftp="NO")
    parser.add_option("--do-smtp", dest="do_smtp", action="store_const", const="YES",
            help = "Extract smtp data")
    parser.set_defaults(do_smtp="NO")
    parser.add_option("--do-irc", dest="do_irc", action="store_const", const="YES",
            help = "Summarize IRC and do irc detail (same as --do-irc-detail and --do-irc-summary)")
    parser.set_defaults(do_irc="NO")
    parser.add_option("--do-irc-summary", dest="do_irc_summary", action="store_const", const="YES",
            help = "Sumarize IRC messages, providing a hit count for key words, use --words to supply a word file")
    parser.set_defaults(do_irc_summary="NO")
    parser.add_option("--do-irc-detail", dest="do_irc_detail", action="store_const", const="YES",
            help = "Extract IRC sessions, do detailed IRC analysis")
    parser.set_defaults(do_irc_detail="NO")
    parser.add_option("--do-sebek", dest="do_sebek", action="store_const", const="YES",
            help = "Summarize Sebek")
    parser.set_defaults(do_sebek="NO")
##    parser.add_option("--do-rrd", dest="do_rrd", action="store_const", const="YES",
##            help = "Do RRD, not yet implemented")
##    parser.set_defaults(do_rrd="NO")

    return parser.parse_args()


   
    
def main():
    cmdparser = OptionParser(option_class=MyOption)
    values, args = configOptions(cmdparser)
    if len(sys.argv) > 1:
        if values.config:
            parser = SafeConfigParser()
            parser.read(values.config)
            config = values.config
            if values.outputdir == "/tmp/analysis":
                try:
                    outputdir = parser.get("IO", "OUTPUT_DATA_DIRECTORY")
                    values.outputdir = outputdir
                except ConfigParser.Error:
                    outputdir = values.outputdir
            if values.tmpdir == "/tmp":
                try:
                    tmpdir = parser.get("IO", "TMP_FILE_DIRECTORY")
                    values.tmpdir = tmpdir
                except ConfigParser.Error:
                    tmpdir = values.tmpdir
            if not values.honeypots:
                try:
                    honeypots = parser.get("IO", "HONEYPOTS")
                    honeypots = honeypots.split()
                    values.honeypots = honeypots
                except ConfigParser.Error:
                    honeypots = values.honeypots
            if not values.wordfile:
                try:
                    wordfile = parser.get("IO", "WORDFILE")
                    values.wordfile = wordfile
                except ConfigParser.Error:
                    values.wordfile = None
            if values.filename is None:
                try:
                    fn = parser.get("IO", "OUTPUTFILE")
                    values.filename = fn
                except ConfigParser.Error:
                    pass
        else:
            parser = None
        dbargs = None
        """
        if parser.has_section("DATABASE"):
            dbargs = {}
            dbargs["host"] = parser.get("DATABASE", "host")
            dbargs["user"] = parser.get("DATABASE", "user")
            dbargs["password"] = parser.get("DATABASE", "password")
            dbargs["db"] = parser.get("DATABASE", "db")
        """

        # pull in the values from the option parser
        options = values.__dict__

        if options['config'] is not None:
            for i in parser.items("OPTIONS"):
                options[i[0]] = i[1]

        options["output_data_directory"] = values.outputdir
        options["tmp_file_directory"] = values.tmpdir
        if not os.path.exists(values.outputdir):
            try:
                os.mkdir(values.outputdir)
            except OSError:
                print "Unable to create output dir: %s. Check permissions." % values.outputdir
                
        if values.honeypots is None:
            print "No honeypots specified. Please use either -H or config file to specify honeypots.\n"
            sys.exit(2)
        hsingleton = HoneysnapSingleton.getInstance(options)
        # by default treat args as files to be processed
        # handle multiple files being passed as args
        if len(args):
            for f in args:
                if os.path.exists(f) and os.path.isfile(f):
                    processFile(values.honeypots, f, dbargs)
                else:
                    print "File not found: %s" % values.files
                    sys.exit(2)
        # -d was an option
        elif values.files:
            if os.path.exists(values.files) and os.path.isdir(values.files):
                for root, dirs, files in os.walk(values.files):
                    #print root, dirs, files
                    if fnmatch(root, values.mask):
                        # this root matches the mask function
                        # find all the log files
                        f  = [j for j in files if fnmatch(j, "log*")]
                        # process each log file
                        if len(f):
                            for i in f:
                                processFile(values.honeypots, root+"/"+i, dbargs)
            else:
                print "File not found: %s" % values.files
                sys.exit(2)
        # no args indicating files, read from stdin
        else:
            # can't really do true stdin input, since we repeatedly parse
            # the file, so create a tempfile that is read from stdin
            # pass it to processFile
            fh = sys.stdin
            tmph, tmpf = tempfile.mkstemp()
            tmph = open(tmpf, 'wb')
            for l in fh:
                tmph.write(l)
            tmph.close()
            processFile(values.honeypots, tmpf, dbargs)
            # all done, delete the tmp file
            os.unlink(tmpf)
##        else:
##            cmdparser.print_help()
        cleanup(options)
    else:
        cmdparser.print_help()

if __name__ == "__main__":
    #import profile
    #profile.run('main()', 'mainprof')
        
    main()
