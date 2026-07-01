package fixtures;

import org.junit.platform.suite.api.ExcludeTags;
import org.junit.platform.suite.api.IncludeTags;
import org.junit.platform.suite.api.Suite;

@Suite
@IncludeTags({"happy", "stream-v2", "stream-any", "ignored"})
@ExcludeTags({"ignored"})
public class MiniRunner {
}
