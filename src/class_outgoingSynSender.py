import threading
import time
import random
import shared
import socks
from i2p import socket
import sys
import tr

from class_sendDataThread import *
from class_receiveDataThread import *

# For each stream to which we connect, several outgoingSynSender threads
# will exist and will collectively create 8 connections with peers.

class outgoingSynSender(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)

    def setup(self, streamNumber, selfInitiatedConnections):
        self.streamNumber = streamNumber
        self.selfInitiatedConnections = selfInitiatedConnections

    def _getPeer(self):
        # If the user has specified a trusted peer then we'll only
        # ever connect to that. Otherwise we'll pick a random one from
        # the known nodes
        shared.knownNodesLock.acquire()
        if shared.trustedPeer:
            peer = shared.trustedPeer
            shared.knownNodes[self.streamNumber][peer] = time.time()
        else:
            try:
                peer, = random.sample(shared.knownNodes[self.streamNumber], 1)
            except ValueError:
                peer = None
        shared.knownNodesLock.release()

        return peer

    def run(self):
        while shared.safeConfigGetBoolean('bitmessagesettings', 'dontconnect'):
            time.sleep(2)
        while shared.safeConfigGetBoolean('bitmessagesettings', 'sendoutgoingconnections'):
            maximumConnections = 1 if shared.trustedPeer else 8 # maximum number of outgoing connections = 8
            while len(self.selfInitiatedConnections[self.streamNumber]) >= maximumConnections:
                time.sleep(10)
            if shared.shutdown:
                break
            random.seed()
            peer = self._getPeer()
            if not peer:
                break
            shared.alreadyAttemptedConnectionsListLock.acquire()
            while peer in shared.alreadyAttemptedConnectionsList or peer.dest in shared.connectedHostsList:
                shared.alreadyAttemptedConnectionsListLock.release()
                # print 'choosing new sample'
                random.seed()
                peer = self._getPeer()
                time.sleep(1)
                if not peer:
                    break
                # Clear out the shared.alreadyAttemptedConnectionsList every half
                # hour so that this program will again attempt a connection
                # to any nodes, even ones it has already tried.
                if (time.time() - shared.alreadyAttemptedConnectionsListResetTime) > 1800:
                    shared.alreadyAttemptedConnectionsList.clear()
                    shared.alreadyAttemptedConnectionsListResetTime = int(
                        time.time())
                shared.alreadyAttemptedConnectionsListLock.acquire()
            shared.alreadyAttemptedConnectionsList[peer] = 0
            shared.alreadyAttemptedConnectionsListLock.release()

            sock = socks.socksocket(shared.i2psession, socket.SOCK_STREAM)

            # This option apparently avoids the TIME_WAIT state so that we
            # can rebind faster
            # sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(600)
            if shared.config.get('bitmessagesettings', 'socksproxytype') == 'none' and shared.verbose >= 2:
                with shared.printLock:
                    print 'Trying an outgoing connection to', peer

            try:
                sock.connect(peer.dest)
                rd = receiveDataThread()
                rd.daemon = True  # close the main program even if there are threads left
                someObjectsOfWhichThisRemoteNodeIsAlreadyAware = {} # This is not necessairly a complete list; we clear it from time to time to save memory.
                sendDataThreadQueue = Queue.Queue() # Used to submit information to the send data thread for this connection. 
                rd.setup(sock, 
                         peer.dest, 
                         self.streamNumber,
                         someObjectsOfWhichThisRemoteNodeIsAlreadyAware, 
                         self.selfInitiatedConnections, 
                         sendDataThreadQueue)
                rd.start()
                with shared.printLock:
                    print self, 'connected to', peer, 'during an outgoing attempt.'


                sd = sendDataThread(sendDataThreadQueue)
                sd.setup(sock, peer.dest, self.streamNumber,
                         someObjectsOfWhichThisRemoteNodeIsAlreadyAware)
                sd.start()
                sd.sendVersionMessage()

            except socks.GeneralProxyError as err:
                if shared.verbose >= 2:
                    with shared.printLock:
                        print 'Could NOT connect to', peer, 'during outgoing attempt.', err

                deletedPeer = None
                with shared.knownNodesLock:
                    """
                    It is remotely possible that peer is no longer in shared.knownNodes.
                    This could happen if two outgoingSynSender threads both try to 
                    connect to the same peer, both fail, and then both try to remove
                    it from shared.knownNodes. This is unlikely because of the
                    alreadyAttemptedConnectionsList but because we clear that list once
                    every half hour, it can happen.
                    """
                    if peer in shared.knownNodes[self.streamNumber]:
                        timeLastSeen = shared.knownNodes[self.streamNumber][peer]
                        if (int(time.time()) - timeLastSeen) > 172800 and len(shared.knownNodes[self.streamNumber]) > 1000:  # for nodes older than 48 hours old if we have more than 1000 hosts in our list, delete from the shared.knownNodes data-structure.
                            del shared.knownNodes[self.streamNumber][peer]
                            deletedPeer = peer
                if deletedPeer:
                    with shared.printLock:
                        print 'deleting', peer, 'from shared.knownNodes because it is more than 48 hours old and we could not connect to it.'

            except socket.Error as err:
                if shared.verbose >= 1:
                    with shared.printLock:
                        print 'Could NOT connect to', peer, 'during outgoing attempt.', err

                deletedPeer = None
                with shared.knownNodesLock:
                    """
                    It is remotely possible that peer is no longer in shared.knownNodes.
                    This could happen if two outgoingSynSender threads both try to 
                    connect to the same peer, both fail, and then both try to remove
                    it from shared.knownNodes. This is unlikely because of the
                    alreadyAttemptedConnectionsList but because we clear that list once
                    every half hour, it can happen.
                    """
                    if peer in shared.knownNodes[self.streamNumber]:
                        timeLastSeen = shared.knownNodes[self.streamNumber][peer]
                        if (int(time.time()) - timeLastSeen) > 172800 and len(shared.knownNodes[self.streamNumber]) > 1000:  # for nodes older than 48 hours old if we have more than 1000 hosts in our list, delete from the shared.knownNodes data-structure.
                            del shared.knownNodes[self.streamNumber][peer]
                            deletedPeer = peer
                if deletedPeer:
                    with shared.printLock:
                        print 'deleting', peer, 'from shared.knownNodes because it is more than 48 hours old and we could not connect to it.'

            except Exception as err:
                sys.stderr.write(
                    'An exception has occurred in the outgoingSynSender thread that was not caught by other exception types: ')
                import traceback
                traceback.print_exc()
            time.sleep(0.1)
            
