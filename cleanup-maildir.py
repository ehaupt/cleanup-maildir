#!/usr/bin/python3 -tt
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

EXAMPLES
  # Archive messages in 'Sent Items' folder over 30 days old
  cleanup-maildir --age=30 archive 'Sent Items'"

  # Delete messages over 2 weeks old in 'Lists/debian-devel' folder,
  # except messages that are part of a thread containing a flagged message.
  cleanup-maildir --keep-flagged-threads trash 'Lists.debian-devel'
"""

__version__ = "0.3.0"
# $Id$
# $URL$

# pip3 install git+https://github.com/jciskey/pygraph

import pygraph
import email
import email.policy
import mailbox
import getopt
import logging
import os
import os.path
import re
import sys
from datetime import datetime, timedelta


class MessageDateError(TypeError):
    """Indicate that the message date was invalid"""
    pass


class MaildirMessage(mailbox.MaildirMessage):
    """Represents an email message

    Used as the message factory for mailbox.Maildir.
    Has extra Maildir-specific attributes that are used while scanning
    the messages in a folder.
    """

    def __init__(self, f):
        """f is a file pointer to a binary file.
        Unfortunately mailbox does not allow setting the email policy
        when creating a Maildir. mailbox.MaildirMessage uses the
        compat32 policy by default. Therefore the message must be
        created here using the correct policy (EmailPolicy), and then
        passed to super for initialization.
        """
        super().__init__(email.message_from_binary_file(f, policy = email.policy.default))

    def isFlagged(self):
        """return true if the message is flagged as important"""
        return 'F' in self.get_flags()

    def isUnread(self):
        """return true if the message is marked as not seen"""
        return not 'S' in self.get_flags()

    def getSubject(self):
        """get the message's subject as a unicode string"""

        return self.get("Subject")

    def getSubjectHash(self):
        """get the message's subject in a "normalized" form

        This currently means lowercasing and removing any reply or forward
        indicators.
        """
        s = self.getSubject()
        if s == None:
            return '(no subject)'
        return re.sub(r'^(re|fwd?):\s*', '', s.lower().strip())

    def getMessageId(self):
        return self.get('Message-ID')

    def getInReplyTo(self):
        irt = self.get('In-Reply-To')
        if irt is None:
            return None
        # Handle an empty In-Reply-To gracefully (RT does generate those).
        if len(irt.strip()) == 0:
            return None
        return irt

    def getReferences(self):
        references = self.get('References')
        if references is None:
            return []
        # remove commas between references before splitting
        references = re.sub(r'>\s*,\s*<', '> <', references).strip()
        return [mid for mid in re.split('\s+', references) if mid[0] == '<' and mid[-1] == '>']

    def getDateSent(self):
        """Get the time of sending from the Date header

        Returns a datetime object using datetime.strptime.  Not very reliable, because
        the Date header can be missing or spoofed (and often is, by spammers).
        Throws a MessageDateError if the Date header is missing or invalid.
        """
        dh = self.get('Date')
        if dh == None:
            return None
        try:
            # Mon,  5 Oct 2020 06:25:09 +0200 (CEST)
            return datetime.strptime(dh, '%a, %d %b %Y %H:%M:%S %z')
        except ValueError:
            raise MessageDateError("message has missing or bad Date")
        except OverflowError:
            raise MessageDateError("message has missing or bad Date")

    def getDateRecd(self):
        """Get the datetime the message was received"""
        return datetime.fromtimestamp(self.get_date())

    def getDateSentOrRecd(self):
        """Get the datetime the message was sent, fall back on time received"""
        try:
            d = self.getDateSent()
            if d != None:
                return d
        except MessageDateError:
            pass
        return self.getDateRecd()

    def getAge(self):
        """Get the timedelta since the message was received"""
        msgTime = self.getDateRecd()
        msgAge = datetime.now() - msgTime
        return msgAge


class Graph(pygraph.classes.UndirectedGraph):
    """A specialization  that allows identifying nodes and edges using message ids.
    (The base UndirectedGraph uses internal ids for nodes and edges)"""

    def new_node(self, node_id):
        """Add a new node with the specified node_id, if one doesn't exist"""

        # check for existing node
        if self.has_node(node_id):
            return node_id

        # create a new node
        node = {'id': node_id,
                'edges': [],
                'data': {}
        }
        self.nodes[node_id] = node
        self._num_nodes += 1
        return node_id

    def has_node(self, node_id):
        """Return true if the node exists"""
        try:
            return self.get_node(node_id)
        except pygraph.NonexistentNodeError:
            return False

    def new_edge(self, node_a, node_b):
        """Add a new edge between the specified nodes, if one doesn't exist.

        The nodes must exist, otherwise a pygraph.NonexistentNodeError
        exception might be thrown."""

        # check for existing edge
        edge_id = self.get_first_edge_id_by_node_ids(node_a, node_b)
        if edge_id is not None:
            return edge_id

        # create new edge
        return super().new_edge(node_a, node_b)

    def related(self, node_id):
        """Return the ids of all nodes that are related to the specified node,
        i.e. all messages in the same thread.
        """
        node = self.get_node(node_id)
        yield from self._related(node_id, set([node_id]))

    def _related(self, node_id, visited):
        for nid in self.neighbors(node_id):
            if not nid in visited:
                yield nid
                visited.add(nid)
                yield from self._related(nid, visited)


class MaildirCleaner(object):

    """Clean a maildir by deleting or moving old messages"""

    __trashDir = None
    stats = {'total': 0, 'delete': 0, 'trash': 0, 'archive': 0, 'recent': 0, 'flagged': 0, 'unread': 0, 'read': 0, 'related': 0}
    keepMsgIds = {}
    relatedMsgIds = {}
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
        self.logger = logging.getLogger('MaildirCleaner')
        self.logger.setLevel(logging.DEBUG)

    def __getTrashDir(self):
        if not self.__trashDir:
            path = os.path.join(self.folderBase, self.folderPrefix + self.trashFolder)
            self.__trashDir = mailbox.Maildir(path)
        return self.__trashDir

    trashDir = property(__getTrashDir)

    def scanThreads(self, maildir):
        """Scans for flagged messages and related messages in thread"""
        self.logger.info("Scanning threads...")
        references = Graph()

        # Need to iterate over keys and explicitly do a get_message to initialize flags.
        for i, msg_key in enumerate(maildir.iterkeys()):
            if i % 1000 == 0:
                self.logger.debug("Processed %d mails...", i)

            msg = maildir.get_message(msg_key)
            mid = msg.getMessageId()
            if mid is None:
                self.logger.debug("Mail without a message ID found (%d): %s", i, msg.getSubjectHash())
                continue

            if self.keepFlaggedThreads and msg.isFlagged():
                self.keepMsgIds[mid] = 1
                self.logger.debug("Flagged #%d: %s -- %s", i, msg.getSubjectHash(), mid)

            if self.keepUnreadThreads and msg.isUnread():
                self.keepMsgIds[mid] = 1
                self.logger.debug("Unread #%d: %s -- %s", i, msg.getSubjectHash(), mid)

            # build references graph
            references.new_node(mid)

            irt = msg.getInReplyTo()
            if irt is not None:
                references.new_node(irt)
                references.new_edge(mid, irt)

            # Add references header as well, as intermediate messages
            # might be saved in the Sent folder.
            for ref in msg.getReferences():
                references.new_node(ref)
                references.new_edge(mid, ref)

        # collect related messages using references graph
        for wmid in self.keepMsgIds.keys():
            for tmid in references.related(wmid):
                self.relatedMsgIds[tmid] = 1
                self.logger.debug("Relative: %s (related to) %s", tmid, wmid)

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

        archiveFolder = self.archiveFolder
        if archiveFolder == None:
            if folderName == 'INBOX':
                archiveFolder = ""
            else:
                archiveFolder = folderName

        if folderName == 'INBOX':
            path = self.folderBase
        else:
            path = os.path.join(self.folderBase, self.folderPrefix + folderName)

        maildir = mailbox.Maildir(path, MaildirMessage)

        fakeMsg = ""
        if self.isTrialRun:
            fakeMsg = "(Not really) "

        # scan for threads to keep
        if self.keepFlaggedThreads or self.keepUnreadThreads:
            self.scanThreads(maildir)

        # Move old messages
        # Need to iterate over keys and explicitly do a get_message to initialize flags.
        # Also, the message key is needed for removing the message.
        for i, msg_key in enumerate(maildir.iterkeys()):
            msg = maildir.get_message(msg_key)
            mid = msg.getMessageId()
            if mid in self.keepMsgIds:
                if msg.isFlagged():
                    self.stats['flagged'] += 1
                    self.log(logging.DEBUG, "Keeping #%d (flagged)" % i, msg)
                else: # msg.isUnread()
                    self.stats['unread'] += 1
                    self.log(logging.DEBUG, "Keeping #%d (unread)" % i, msg)
            elif mid in self.relatedMsgIds:
                self.stats['related'] += 1
                self.log(logging.DEBUG, "Keeping #%d (part of kept thread)" % i, msg)
            elif self.keepRead and not msg.isUnread():
                self.log(logging.DEBUG, "Keeping #%d (read)" % i, msg)
                self.stats['read'] += 1
            elif msg.getAge() < minAge:
                self.log(logging.DEBUG, "Keeping #%d (recent)" % i, msg)
                self.stats['recent'] += 1
            else:
                if mode == 'trash':
                    self.log(logging.INFO, "%sTrashing #%d (old)" % (fakeMsg, i), msg)
                    if not self.isTrialRun:
                        maildir.remove(msg_key)
                        self.trashDir.add(msg)
                elif mode == 'delete':
                    self.log(logging.INFO, "%sDeleting #%d (old)" % (fakeMsg, i), msg)
                    if not self.isTrialRun:
                        maildir.remove(msg_key)
                else: # mode == 'archive'
                    # Determine subfolder path
                    mdate = msg.getDateSentOrRecd()
                    datePart = '%04d' % mdate.year
                    if self.archiveHierDepth > 1:
                        datePart += self.folderSeperator + ('%02d' % mdate.month)
                    if self.archiveHierDepth > 2:
                        datePart += self.folderSeperator + ('%02d' % mdate.day)
                    subFolder = archiveFolder + self.folderSeperator + datePart
                    sfPath = os.path.join(self.folderBase, self.folderPrefix + subFolder)
                    self.log(logging.INFO, "%sArchiving #%d to %s" % (fakeMsg, i, subFolder), msg)
                    if not self.isTrialRun:
                        md = mailbox.Maildir(sfPath)
                        maildir.remove(msg_key)
                        md.add(msg)
                self.stats[mode] += 1

            self.stats['total'] += 1

    def log(self, lvl, text, msgObj):
        """Log some text with the subject of a message"""
        subj = msgObj.getSubject()
        if subj == None:
            subj = "(no subject)"
        self.logger.log(lvl, text + ": " + subj)


# Defaults
minAge = timedelta(days = 14)
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
            ["help", "quiet", "verbose", "version", "trash-folder=",
             "age=", "keep-flagged-threads", "keep-unread-threads",
             "keep-read", "folder-seperator=", "folder-prefix=",
             "maildir-root=", "archive-folder=", "archive-hierarchy-depth=",
             "trial-run"])
except getopt.GetoptError(msg, opt):
    logger.error("%s\n\n%s" % (msg, __doc__))
    sys.exit(2)
output = None
for o, a in opts:
    if o in ("-h", "--help"):
        print(__doc__)
        sys.exit()
    if o in ("-q", "--quiet"):
        logging.disable(logging.WARNING - 1)
    if o in ("-v", "--verbose"):
        logging.disable(logging.DEBUG - 1)
    if o == "--version":
        print(__version__)
        sys.exit()
    if o in ("-n", "--trial-run"):
        cleaner.isTrialRun = True
    if o in ("-t", "--trash-folder"):
        cleaner.trashFolder = a
    if o == "--archive-folder":
        cleaner.archiveFolder = a
    if o in ("-a", "--age"):
        minAge = timedelta(days = int(a))
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
    logger.info("Cleaning up %s..." % dir)
    cleaner.clean(mode, dir, minAge)

logger.info('Total messages:     %5d' % cleaner.stats['total'])
logger.info('Untouched messages: %5d' % (cleaner.stats['total'] - cleaner.stats[mode]))
if cleaner.keepFlaggedThreads:
    logger.info('  Flagged:  %5d' % cleaner.stats['flagged'])
if cleaner.keepUnreadThreads:
    logger.info('  Unread:   %5d' % cleaner.stats['unread'])
if cleaner.keepFlaggedThreads or cleaner.keepUnreadThreads:
    logger.info('  Related:  %5d' % cleaner.stats['related'])
if cleaner.keepRead:
    logger.info('  Read:     %5d' % cleaner.stats['read'])
logger.info('  Recent:   %5d' % cleaner.stats['recent'])
logger.info('Affected messages:  %5d' % cleaner.stats[mode])
