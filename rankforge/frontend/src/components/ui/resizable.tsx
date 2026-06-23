"use client";

import { GripVertical } from "lucide-react";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import * as React from "react";

import { cn } from "@/lib/utils";

function ResizablePanelGroup({
  className,
  ...props
}: React.ComponentProps<typeof PanelGroup>) {
  return (
    <PanelGroup
      className={cn(
        "flex h-full w-full data-[panel-group-direction=vertical]:flex-col",
        className
      )}
      {...props}
    />
  );
}

const ResizablePanel = Panel;

function ResizableHandle({
  withHandle = true,
  className,
  ...props
}: React.ComponentProps<typeof PanelResizeHandle> & { withHandle?: boolean }) {
  return (
    <PanelResizeHandle
      className={cn(
        "relative flex w-px items-center justify-center bg-border transition-colors",
        "hover:bg-[rgb(var(--ember))] data-[resize-handle-state=drag]:bg-[rgb(var(--ember))]",
        "data-[panel-group-direction=vertical]:h-px data-[panel-group-direction=vertical]:w-full",
        className
      )}
      {...props}
    >
      {withHandle && (
        <div className="z-10 flex h-6 w-3 items-center justify-center rounded-sm border border-border bg-card">
          <GripVertical className="size-2.5 text-muted-foreground" />
        </div>
      )}
    </PanelResizeHandle>
  );
}

export { ResizablePanelGroup, ResizablePanel, ResizableHandle };
