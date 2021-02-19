# cleanup-maildir

Script for cleaning up and archiving mails in Maildir folders based on arrival date.
This script was originally found [here](https://gist.github.com/pkern/3730543).

The version of cleanup-maildir.py in this repo is ported to Python 3.8 by
[@mhyllander](https://github.com/mhyllander/cleanup-maildir) and uses standard 
modules to interface with Maildir folders:

* email
* mailbox
* datetime

In addition, you need to install the <strong>pygraph</strong> module. The
version that is installed by pip3 from PyPI is currently not compatible
with Python 3. Instead you should install the Python 3-compatible version
directly from the git repo:

```bash
pip3 install git+https://github.com/jciskey/pygraph
```

The command options and arguments remain the same, except that the
deprecated "--mode" option has been removed. Aside from this it is a
drop-in replacement for the Python 2.7 version.
