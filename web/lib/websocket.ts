/**
 * WebSocket Singleton Manager
 * Ensures only ONE WebSocket connection exists globally, regardless of React re-renders
 */

type WebSocketMessage = any;
type MessageHandler = (data: WebSocketMessage) => void;

class WebSocketManager {
  private static instance: WebSocketManager | null = null;
  private ws: WebSocket | null = null;
  private connecting: boolean = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private messageHandlers: Set<MessageHandler> = new Set();
  private url: string = '';

  private constructor() {
    // Private constructor to prevent direct instantiation
  }

  static getInstance(): WebSocketManager {
    if (!WebSocketManager.instance) {
      WebSocketManager.instance = new WebSocketManager();
    }
    return WebSocketManager.instance;
  }

  connect(url: string): void {
    // If already connected to this URL, do nothing
    if (this.ws?.readyState === WebSocket.OPEN && this.url === url) {
      console.log('[WS Singleton] Already connected to', url);
      return;
    }

    // If connecting, do nothing
    if (this.connecting) {
      console.log('[WS Singleton] Connection already in progress');
      return;
    }

    // Store URL
    this.url = url;

    // Close existing connection if any
    if (this.ws) {
      console.log('[WS Singleton] Closing existing connection');
      this.ws.onclose = null; // Prevent reconnect
      this.ws.onerror = null;
      this.ws.onmessage = null;
      this.ws.onopen = null;
      if (this.ws.readyState !== WebSocket.CLOSED) {
        this.ws.close();
      }
      this.ws = null;
    }

    // Clear reconnect timer
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    console.log('[WS Singleton] Creating new connection to', url);
    this.connecting = true;

    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      console.log('[WS Singleton] Connected successfully');
      this.connecting = false;
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Notify all registered handlers
        this.messageHandlers.forEach((handler) => {
          try {
            handler(data);
          } catch (err) {
            console.error('[WS Singleton] Error in message handler:', err);
          }
        });
      } catch (err) {
        console.error('[WS Singleton] Failed to parse message:', err);
      }
    };

    ws.onerror = (error) => {
      console.log('[WS Singleton] Error event:', error);
      this.connecting = false;
    };

    ws.onclose = (event) => {
      console.log('[WS Singleton] Connection closed, code:', event.code, 'reason:', event.reason);
      this.connecting = false;
      this.ws = null;

      // Auto-reconnect after 3 seconds
      if (!this.reconnectTimer) {
        console.log('[WS Singleton] Scheduling reconnect in 3s');
        this.reconnectTimer = setTimeout(() => {
          this.reconnectTimer = null;
          this.connect(this.url);
        }, 3000);
      }
    };
  }

  disconnect(): void {
    console.log('[WS Singleton] Disconnecting');

    // Clear reconnect timer
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    // Close connection
    if (this.ws) {
      this.ws.onclose = null; // Prevent auto-reconnect
      this.ws.onerror = null;
      this.ws.onmessage = null;
      this.ws.onopen = null;
      if (this.ws.readyState !== WebSocket.CLOSED) {
        this.ws.close();
      }
      this.ws = null;
    }

    this.connecting = false;
  }

  send(data: any): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    } else {
      console.warn('[WS Singleton] Cannot send, not connected');
    }
  }

  addMessageHandler(handler: MessageHandler): void {
    this.messageHandlers.add(handler);
  }

  removeMessageHandler(handler: MessageHandler): void {
    this.messageHandlers.delete(handler);
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

// Export singleton instance
export const websocketManager = WebSocketManager.getInstance();
