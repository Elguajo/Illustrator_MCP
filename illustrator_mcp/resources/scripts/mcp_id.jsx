/**
 * mcp_id.jsx — MCP ID Utilities
 * Part of Illustrator MCP Standard Library
 *
 * Single source of truth for MCP ID operations:
 * - Extracting IDs from item notes
 * - Setting IDs in item notes
 * - Removing IDs from notes
 *
 * CONVENTION
 *   IDs are stored in item.note as: @mcp:id=<id>
 *   Multiple tags may exist in a note, space-separated.
 *
 * @version 1.0.0
 */

// ==================== ID Extraction ====================

/**
 * Extract MCP ID from an item's note string.
 * @param {string} note - The item.note string
 * @returns {string|null} The ID, or null if not found
 */
function extractMcpId(note) {
    if (!note) return null;
    var match = note.match(/@mcp:id=([^\s@]+)/);
    return match ? match[1] : null;
}

/**
 * Set (or replace) the MCP ID in an item's note.
 * If an existing @mcp:id tag is present, it is replaced.
 * Otherwise the tag is appended.
 *
 * @param {PageItem} item - The Illustrator item
 * @param {string} id - The MCP ID to set
 */
function setMcpId(item, id) {
    if (!item || !id) return;
    var note = item.note || "";
    if (note.match(/@mcp:id=[^\s@]+/)) {
        // Replace existing
        item.note = note.replace(/@mcp:id=[^\s@]+/, "@mcp:id=" + id);
    } else {
        // Append
        item.note = (note ? note + " " : "") + "@mcp:id=" + id;
    }
}

/**
 * Remove the @mcp:id tag from a note string.
 * Returns the cleaned note.
 *
 * @param {string} note - The item.note string
 * @returns {string} Note with @mcp:id tag removed
 */
function removeIdFromNote(note) {
    if (!note) return "";
    return note.replace(/@mcp:id=[^\s@]+/, "").replace(/\s{2,}/g, " ").replace(/^\s+|\s+$/g, "");
}

// ==================== Native uuid resolution ====================

/**
 * Resolve Illustrator's native PageItem.uuid (AI 24+) to its PageItem.
 *
 * Three behaviours observed live on Illustrator 30.8.1 shape this function:
 *  - getPageItemFromUuid() THROWS on an unknown uuid instead of returning
 *    null, so a miss is caught and reported as null.
 *  - For a CompoundPathItem it returns a different object typed GroupItem
 *    (same uuid, same parent). Code that then sets fillColor on that
 *    "group" fails or silently does nothing. The real CompoundPathItem is
 *    recovered from the wrapper's parent by uuid.
 *  - It ignores its receiver and resolves in app.activeDocument, and uuids
 *    collide across open documents. A hit owned by any document other than
 *    doc is therefore reported as null, never returned.
 *
 * @param {Document} doc
 * @param {string} uuid
 * @returns {PageItem|null}
 */
function resolvePageItemByUuid(doc, uuid) {
    var key = String(uuid);
    var found = null;
    try {
        found = doc.getPageItemFromUuid(key);
    } catch (e) {
        return null;
    }
    if (!found) return null;
    if (ownerDocumentOf(found) !== doc) return null;
    if (found.typename === "GroupItem") {
        try {
            var siblings = found.parent.compoundPathItems;
            for (var i = 0; i < siblings.length; i++) {
                if (String(siblings[i].uuid) === key) return siblings[i];
            }
        } catch (e2) {
            // Parent without compoundPathItems: the GroupItem is genuine.
        }
    }
    return found;
}

/**
 * Walk item.parent up to the owning Document.
 * Returns null when the chain cannot be read, so callers fail closed.
 * @param {PageItem} item
 * @returns {Document|null}
 */
function ownerDocumentOf(item) {
    try {
        var cur = item.parent;
        // Bounded: nesting depth is far below this; it only guards a cycle.
        for (var depth = 0; cur && depth < 1000; depth++) {
            if (cur.typename === "Document") return cur;
            cur = cur.parent;
        }
    } catch (e) {
        // An unreadable parent chain is not proof of ownership.
    }
    return null;
}
