export class WebSocketService {
    /**
     * @param {string} socketUrl - The URL for the Socket.IO server (optional)
     */
    constructor(socketUrl = '') {
      // If socketUrl is omitted, Socket.IO will connect to the current host
      this.socket = io(socketUrl);
    }
  
    onConnect(callback) {
      this.socket.on('connect', callback);
    }
  
    onNewBar(callback) {
      this.socket.on('new_bar', callback);
    }
  }