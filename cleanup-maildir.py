#!/usr/local/bin/python2.7 -tt
# vim:set et ts=4 sw=4 ai:

"""
USAGE
  cleanup-maildir [OPTION].. COMMAND FOLDERNAME..

DESCRIPTION
  Cleans up old messages in FOLDERNAME; the exact action taken
  depends on COMMAND.  (See next section.)
      Note that FOLDERNAME is a name such as 'Drafts', and the
  corresponding maildir path is determined using the values of
  maildir-root, folder-prefix, and folder-seperator.

COMMANDS
  archive - move old messages to subfolders based on message date
  trash   - move old message to trash folder
  delete  - permanently delete old messages

OPTIONS
  -h, --help
      Show this help.
  -q, --quiet
      Suppress normal output.
  -v, --verbose
      Output extra information for testing.
  -n, --trial-run
      Do not actually touch any files; just say what would be done.
  -a, --age=N
      Only touch messages older than N days.  Default is 14 days.
  -k, --keep-flagged-threads
      If any messages in a thread are flagged, do not touch them or
      any other messages in that thread.
  -u, --keep-unread-threads
      If any messages in a thread are unread, do not touch them or any
      other messages in that thread.
  -r, --keep-read
      If any messages are flagged as READ, do not touch them.
  -t, --trash-folder=F
      Use F as trash folder when COMMAND is 'trash'.
      Default is 'Trash'.
  --archive-folder=F
      Use F as the base for constructing archive folders.  For example, if F is
      'Archive', messages from 2004 might be put in the folder 'Archive.2004'.
  -d, --archive-hierarchy-depth=N
      Specify number of subfolders in archive hierarchy; 1 is just
      the year, 2 is year/month (default), 3 is year/month/day.
  --maildir-root=F
      Specifies folder that contains mail folders.
      Default is "$HOME/Maildir".
  --folder-seperator=str
      Folder hierarchy seperator.  Default is '.'
  --folder-prefix=str
      Folder prefix.  Default is '.'

NOTES
  The following form is accepted for backwards compatibility, but is deprecated:
  cleanup-maildir --mode=COMMAND [OPTION].. FOLDERNAME..

EXAMPLES
  # Archive messages in 'Sent Items' folder over 30 days old
  cleanup-maildir --age=30 archive 'Sent Items'" 

  # Delete messages over 2 weeks old in 'Lists/debian-devel' folder,
  # except messages that are part of a thread containing a flagged message.
  cleanup-maildir --keep-flagged-threads trash 'Lists.debian-devel'
"""

__version__ = "0.2.3"
# $Id$
# $URL$

from pygraph.classes.graph import graph
from pygraph.algorithms.traversal import traversal
import email.Header
import getopt
import logging
import mailbox
import os
import os.path
import re
import rfc822
import socket
import string
import sys
import time


def mkMaildir(path):
    """Make a Maildir structure rooted at 'path'"""
    os.mkdir(path, 0700)
    os.mkdir(os.path.join(path, 'tmp'), 0700)
    os.mkdir(os.path.join(path, 'new'), 0700)
    os.mkdir(os.path.join(path, 'cur'), 0700)


class MaildirWriter(object):

    """Deliver messages into a Maildir"""

    path = None
    counter = 0

    def __init__(self, path=None):
        """Create a MaildirWriter that manages the Maildir at 'path'

        Arguments:
        path -- if specified, used as the default Maildir for this object
        """
        if path != None:
            if not os.path.isdir(path):
                raise ValueError, 'Path does not exist: %s' % path
            self.path = path
        self.logger = logging.getLogger('MaildirWriter')

    def deliver(self, msg, path=None):
        """Deliver a message to a Maildir

        Arguments:
        msg -- a message object
        path -- the path of the Maildir; if None, uses default from __init__
        """
        if path != None:
            self.path = path
        if self.path == None or not os.path.isdir(self.path):
            raise ValueError, 'Path does not exist'
        tryCount = 1
        srcFile = msg.fp._file.name;
        (dstName, tmpFile, newFile, dstFile) = (None, None, None, None)
        while 1:
            try:
                dstName = "%d.%d_%d.%s" % (int(time.time()), os.getpid(), 
                                           self.counter, socket.gethostname())
                tmpFile = os.path.join(os.path.join(self.path, "tmp"), dstName)
                newFile = os.path.join(os.path.join(self.path, "new"), dstName)
                self.logger.debug("deliver: attempt copy %s to %s" %
                              (srcFile, tmpFile))
                os.link(srcFile, tmpFile) # Copy into tmp
                self.logger.debug("deliver: attempt link to %s" % newFile)
                os.link(tmpFile, newFile) # Link into new
            except OSError, (n, s):
                self.logger.critical(
                        "deliver failed: %s (src=%s tmp=%s new=%s i=%d)" % 
                        (s, srcFile, tmpFile, newFile, tryCount))
                self.logger.info("sleeping")
                time.sleep(2)
                tryCount += 1
                self.counter += 1
                if tryCount > 10:
                    raise OSError("too many failed delivery attempts")
            else:
                break

        # Successful delivery; increment deliver counter
        self.counter += 1

        # For the rest of this method we are acting as an MUA, not an MDA.

        # Move message to cur and restore any flags
        dstFile = os.path.join(os.path.join(self.path, "cur"), dstName)
        if msg.getFlags() != None:
            dstFile += ':' + msg.getFlags()
        self.logger.debug("deliver: attempt link to %s" % dstFile)
        os.link(newFile, dstFile)
        os.unlink(newFile)

        # Cleanup tmp file
        os.unlink(tmpFile)


class MessageDateError(TypeError):
    """Indicate that the message date was invalid"""
    pass


class MaildirMessage(rfc822.Message):
    
    """An email message

    Has extra Maildir-specific attributes
    """

    def isFlagged(self):
        """return true if the message is flagged as important"""
        fname = self.fp._file.name
        if re.search(r':.*F', fname) != None:
            return True
        return False

    def getFlags(self):
        """return the flag part of the message's filename"""
        parts = self.fp._file.name.split(':')
        if len(parts) == 2:
            return parts[1]
        return None

    def isNew(self):
        """return true if the message is marked as unread"""
        # XXX should really be called isUnread
        fname = self.fp._file.name
        if re.search(r':.*S', fname) != None:
            return False
        return True

    def getSubject(self):
        """get the message's subject as a unicode string"""

        s = self.getheader("Subject")
        try:
            return u"".join(map(lambda x: x[0].decode(x[1] or 'ASCII', 'replace'),
                                email.Header.decode_header(s)))
        except(LookupError):
            return s

    def getSubjectHash(self):
        """get the message's subject in a "normalized" form

        This currently means lowercasing and removing any reply or forward
        indicators.
        """
        s = self.getSubject()
        if s == None:
            return '(no subject)'
        return re.sub(r'^(re|fwd?):\s*', '', string.strip(s.lower()))

    def getMessageId(self):
        return self.getheader('Message-ID')

    def getInReplyTo(self):
        irt = self.getheader('In-Reply-To')
        if irt is None:
            return None
        # Handle an empty In-Reply-To gracefully (RT does generate those).
        if len(irt.strip()) == 0:
            return None
        return irt

    def getReferences(self):
        references = self.getheader('References')
        if references is None:
            return []
        return [mid for mid in re.split('\s+', references) if mid[0] == '<' and mid[-1] == '>']

    def getDateSent(self):
        """Get the time of sending from the Date header

        Returns a time object using time.mktime.  Not very reliable, because
        the Date header can be missing or spoofed (and often is, by spammers).
        Throws a MessageDateError if the Date header is missing or invalid.
        """
        dh = self.getheader('Date')
        if dh == None: 
            return None
        try:
            return time.mktime(rfc822.parsedate(dh))
        except ValueError:
            raise MessageDateError("message has missing or bad Date")
        except TypeError:  # gets thrown by mktime if parsedate returns None
            raise MessageDateError("message has missing or bad Date")
        except OverflowError:
            raise MessageDateError("message has missing or bad Date")

    def getDateRecd(self):
        """Get the time the message was received"""
        # XXX check that stat returns time in UTC, fix if not
        return os.stat(self.fp._file.name)[8]

    def getDateSentOrRecd(self):
        """Get the time the message was sent, fall back on time received"""
        try:
            d = self.getDateSent()
            if d != None:
                return d
        except MessageDateError:
            pass
        return self.getDateRecd()

    def getAge(self):
        """Get the number of seconds since the message was received"""
        msgTime = self.getDateRecd()
        msgAge = time.mktime(time.gmtime()) - msgTime
        return msgAge / (60*60*24)


class MaildirCleaner(object):

    """Clean a maildir by deleting or moving old messages"""

    __trashWriter = None
    __mdWriter = None
    stats = {'total': 0, 'delete': 0, 'trash': 0, 'archive': 0}
    keepSubjects = {}
    archiveFolder = None
    archiveHierDepth = 2
    folderBase = None
    folderPrefix = "."
    folderSeperator = "."
    keepFlaggedThreads = False
    keepUnreadThreads = False
    trashFolder = "Trash"
    isTrialRun = False
    keepRead = False

    def __init__(self, folderBase=None):
        """Initialize the MaildirCleaner

        Arguments:
        folderBase -- the directory in which the folders are found
        """
        self.folderBase = folderBase
        self.__mdWriter = MaildirWriter()
        self.logger = logging.getLogger('MaildirCleaner')
        self.logger.setLevel(logging.DEBUG)

    def __getTrashWriter(self):
        if not self.__trashWriter:
            path = os.path.join(self.folderBase, self.folderPrefix + self.trashFolder)
            self.__trashWriter = MaildirWriter(path)
        return self.__trashWriter

    trashWriter = property(__getTrashWriter)

    def scanSubjects(self, folderName):
        """Scans for flagged subjects"""
        self.logger.info("Scanning threads...")
        if (folderName == 'INBOX'):
            path = self.folderBase
        else:
            path = os.path.join(self.folderBase, self.folderPrefix + folderName)
        maildir = mailbox.Maildir(path, MaildirMessage)
        self.keepMsgIds = dict()
        wantedMsgIds = list()
        references = graph()
        for i, msg in enumerate(maildir):
            if i % 1000 == 0:
                self.logger.debug("Processed %d mails...", i)
            mid, irt = msg.getMessageId(), msg.getInReplyTo()
            if mid is None:
                self.logger.debug("Mail without a message ID found (%d): %s", i, msg.getSubjectHash())
                continue
            if not references.has_node(mid):
                references.add_node(mid)
            if not references.has_edge((mid, mid)):
                references.add_edge((mid, mid))
            if irt is not None:
                if not references.has_node(irt):
                    references.add_node(irt)
                if not references.has_edge((mid, irt)):
                    references.add_edge((mid, irt))
                # Add references header as well, as intermediate messages
                # might be saved in the Sent folder.
                for ref in msg.getReferences():
                    if not references.has_node(ref):
                        references.add_node(ref)
                    if not references.has_edge((mid, ref)):
                        references.add_edge((mid, ref))
            if self.keepFlaggedThreads and msg.isFlagged():
                wantedMsgIds.append(mid)
                self.logger.debug("Flagged (%d): %s -- %s", i, msg.getSubjectHash(), mid)
            if self.keepUnreadThreads and msg.isNew():
                wantedMsgIds.append(mid)
                self.logger.debug("Unread (%d): %s -- %s", i, msg.getSubjectHash(), mid)
        for wmid in wantedMsgIds:
            for tmid in traversal(references, wmid, 'pre'):
                self.keepMsgIds[tmid] = 1
                self.logger.debug("Keeping %s (part of wanted %s)", tmid, wmid)
        self.logger.info("Done scanning.")


    def clean(self, mode, folderName, minAge):

        """Trashes or archives messages older than minAge days

        Arguments:
        mode -- the cleaning mode.  Valid modes are:
            trash -- moves the messages to a trash folder
            archive -- moves the messages to folders based on their date
            delete -- deletes the messages
        folderName -- the name of the folder on which to operate
            This is a name like "Stuff", not a filename
        minAge -- messages younger than minAge days are left alone
        """

        if not mode in ('trash', 'archive', 'delete'):
            raise ValueError

        if (self.keepFlaggedThreads or self.keepUnreadThreads):
            self.scanSubjects(folderName)

        archiveFolder = self.archiveFolder
        if (archiveFolder == None):
            if (folderName == 'INBOX'):
                archiveFolder = ""
            else:
                archiveFolder = folderName

        if (folderName == 'INBOX'):
            path = self.folderBase
        else:
            path = os.path.join(self.folderBase, self.folderPrefix + folderName)

        maildir = mailbox.Maildir(path, MaildirMessage)

        fakeMsg = ""
        if self.isTrialRun: 
            fakeMsg = "(Not really) "

        # Move old messages
        for i, msg in enumerate(maildir):
            if self.keepFlaggedThreads == True \
                    and msg.getMessageId() in self.keepMsgIds:
                self.log(logging.DEBUG, "Keeping #%d (topic flagged)" % i, msg)
            else:
                if (msg.getAge() >= minAge) and ((not self.keepRead) or (self.keepRead and msg.isNew())):
                    if mode == 'trash':
                        self.log(logging.INFO, "%sTrashing #%d (old)" %
                                 (fakeMsg, i), msg)
                        if not self.isTrialRun:
                            self.trashWriter.deliver(msg)
                            os.unlink(msg.fp._file.name)
                    elif mode == 'delete':
                        self.log(logging.INFO, "%sDeleting #%d (old)" % 
                                 (fakeMsg, i), msg)
                        if not self.isTrialRun:
                            os.unlink(msg.fp._file.name)
                    else: # mode == 'archive'
                        # Determine subfolder path
                        mdate = time.gmtime(msg.getDateSentOrRecd())
                        datePart = str(mdate[0])
                        if self.archiveHierDepth > 1:
                            datePart += self.folderSeperator \
                                        + time.strftime("%m", mdate)
                        if self.archiveHierDepth > 2:
                            datePart += self.folderSeperator \
                                        + time.strftime("%d", mdate)
                        subFolder = archiveFolder + self.folderSeperator \
                                    + datePart
                        sfPath = os.path.join(self.folderBase, 
                                              self.folderPrefix + subFolder)
                        self.log(logging.INFO, "%sArchiving #%d to %s" %
                                 (fakeMsg, i, subFolder), msg)
                        if not self.isTrialRun:
                            # Create the subfolder if needed
                            if not os.path.exists(sfPath):
                                mkMaildir(sfPath)
                            # Deliver
                            self.__mdWriter.deliver(msg, sfPath)
                            os.unlink(msg.fp._file.name)
                    self.stats[mode] += 1
                else:
                    self.log(logging.DEBUG, "Keeping #%d (fresh)" % i, msg)
            self.stats['total'] += 1

    def log(self, lvl, text, msgObj):
        """Log some text with the subject of a message"""
        subj = msgObj.getSubject()
        if subj == None:
            subj = "(no subject)"
        self.logger.log(lvl, text + ": " + subj)


# Defaults
minAge = 14
mode = None

logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)
logging.disable(logging.INFO - 1)
logger = logging.getLogger('cleanup-maildir')
cleaner = MaildirCleaner()

# Read command-line arguments
try:
    opts, args = getopt.getopt(sys.argv[1:], 
            "hqvnrm:t:a:kud:",
            ["help", "quiet", "verbose", "version", "mode=", "trash-folder=",
             "age=", "keep-flagged-threads", "keep-unread-threads",
             "keep-read", "folder-seperator=", "folder-prefix=",
             "maildir-root=", "archive-folder=", "archive-hierarchy-depth=",
             "trial-run"])
except getopt.GetoptError, (msg, opt):
    logger.error("%s\n\n%s" % (msg, __doc__))
    sys.exit(2)
output = None
for o, a in opts:
    if o in ("-h", "--help"):
        print __doc__
        sys.exit()
    if o in ("-q", "--quiet"): 
        logging.disable(logging.WARNING - 1)
    if o in ("-v", "--verbose"): 
        logging.disable(logging.DEBUG - 1)
    if o == "--version":
        print __version__
        sys.exit()
    if o in ("-n", "--trial-run"):
        cleaner.isTrialRun = True
    if o in ("-m", "--mode"): 
        logger.warning("the --mode flag is deprecated (see --help)")
        if a in ('trash', 'archive', 'delete'):
            mode = a
        else:
            logger.error("%s is not a valid command" % a)
            sys.exit(2)
    if o in ("-t", "--trash-folder"): 
        cleaner.trashFolder = a
    if o == "--archive-folder":
        cleaner.archiveFolder = a
    if o in ("-a", "--age"): 
        minAge = int(a)
    if o in ("-k", "--keep-flagged-threads"): 
        cleaner.keepFlaggedThreads = True
    if o in ("-u", "--keep-unread-threads"): 
        cleaner.keepUnreadThreads = True
    if o in ("-r", "--keep-read"):
        cleaner.keepRead = True
    if o == "--folder-seperator":
        cleaner.folderSeperator = a
    if o == "--folder-prefix":
        cleaner.folderPrefix = a
    if o == "--maildir-root":
        cleaner.folderBase = a
    if o in ("-d", "--archive-hierarchy-depth"): 
        archiveHierDepth = int(a)
        if archiveHierDepth < 1 or archiveHierDepth > 3:
            sys.stderr.write("Error: archive hierarchy depth must be 1, " +
                             "2, or 3.\n")
            sys.exit(2)
        cleaner.archiveHierDepth = archiveHierDepth

if not cleaner.folderBase:
    cleaner.folderBase = os.path.join(os.environ["HOME"], "Maildir")
if mode == None:
    if len(args) < 1:
        logger.error("No command specified")
        sys.stderr.write(__doc__)
        sys.exit(2)
    mode = args.pop(0)
    if not mode in ('trash', 'archive', 'delete'):
        logger.error("%s is not a valid command" % mode)
        sys.exit(2)

if len(args) == 0:
    logger.error("No folder(s) specified")
    sys.stderr.write(__doc__)
    sys.exit(2)

logger.debug("Mode is " + mode)

# Clean each folder
for dir in args:
    logger.debug("Cleaning up %s..." % dir)
    cleaner.clean(mode, dir, minAge)

logger.info('Total messages:     %5d' % cleaner.stats['total'])
logger.info('Affected messages:  %5d' % cleaner.stats[mode])
logger.info('Untouched messages: %5d' %
            (cleaner.stats['total'] - cleaner.stats[mode]))
